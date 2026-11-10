"""Fast parallel disk-usage analyzer.

Directories are scanned by a pool of worker threads pulling from a shared queue
(``os.scandir`` releases the GIL during the system call, so this scales well on
SSDs). Symlinks and junctions are never followed, so nothing is counted twice
and loops are impossible. Results can be exported as JSON or CSV.
"""

from __future__ import annotations

import csv
import heapq
import json
import os
import queue
import stat
import threading
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from pathlib import Path

from disksweep.safety import is_link_or_reparse

ProgressFn = Callable[[int, int, int], None]
"""``progress(dirs_scanned, files_scanned, bytes_seen)``."""


@dataclass
class Entry:
    """A file or folder and its size in bytes."""

    path: str
    bytes: int


@dataclass
class TypeEntry:
    """Total size and count of files sharing an extension (e.g. ``.iso``)."""

    ext: str
    bytes: int
    files: int


NO_EXTENSION = "(no extension)"
OTHER_TYPES = "Other"


def file_type(name: str) -> str:
    """Lower-cased extension of a file name, or :data:`NO_EXTENSION`."""
    ext = os.path.splitext(name)[1].lower()
    return ext if ext and len(ext) <= 12 else NO_EXTENSION


def top_types(totals: dict[str, list[int]], n: int = 8) -> list[TypeEntry]:
    """The ``n`` largest types, with the remainder folded into one "Other" entry."""
    ranked = sorted(totals.items(), key=lambda kv: kv[1][0], reverse=True)
    head = [TypeEntry(ext, b, c) for ext, (b, c) in ranked[:n]]
    rest = ranked[n:]
    if rest:
        head.append(TypeEntry(OTHER_TYPES, sum(v[0] for _, v in rest),
                              sum(v[1] for _, v in rest)))  # fmt: skip
    return head


@dataclass
class AnalysisResult:
    """Result of :func:`analyze`."""

    root: str
    total_bytes: int = 0
    file_count: int = 0
    dir_count: int = 0
    top_dirs: list[Entry] = field(default_factory=list)
    top_files: list[Entry] = field(default_factory=list)
    #: Direct subfolders of ``root``, largest first (drives the drill-down chart).
    children: list[Entry] = field(default_factory=list)
    #: Bytes in files directly inside ``root`` (not in any subfolder).
    root_files_bytes: int = 0
    #: Space per file extension, largest first (``path`` holds the extension label).
    by_type: list[TypeEntry] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    elapsed: float = 0.0

    def to_dict(self) -> dict[str, object]:
        """JSON-serialisable dict."""
        return asdict(self)


@dataclass
class _WorkerState:
    own: dict[str, int] = field(default_factory=dict)
    parent: dict[str, str] = field(default_factory=dict)
    files: list[tuple[int, str]] = field(default_factory=list)
    file_count: int = 0
    errors: list[str] = field(default_factory=list)
    types: dict[str, list[int]] = field(default_factory=dict)


def analyze(
    root: str,
    top: int = 20,
    *,
    workers: int | None = None,
    max_depth: int | None = None,
    cancel: threading.Event | None = None,
    progress: ProgressFn | None = None,
    type_count: int = 8,
) -> AnalysisResult:
    """Compute folder sizes under ``root`` and return the largest folders and files.

    Args:
        root: folder (or drive, e.g. ``C:\\``) to analyse.
        top: how many folders/files to return.
        workers: number of scanning threads (default: ``min(32, 4 * cpu_count)``).
        max_depth: only report folders at most this many levels below ``root``
            (sizes always include everything below).
        cancel: event that stops the scan early (partial results are returned).
        progress: callback invoked periodically from worker threads.
        type_count: file types reported individually before folding into "Other".
    """
    started = time.perf_counter()
    root = os.path.abspath(root)
    if not os.path.isdir(root):
        raise NotADirectoryError(root)
    workers = workers or min(32, (os.cpu_count() or 4) * 4)

    q: queue.Queue[str | None] = queue.Queue()
    q.put(root)
    states = [_WorkerState() for _ in range(workers)]
    counter_lock = threading.Lock()
    counters = {"dirs": 0, "files": 0, "bytes": 0}

    def work(state: _WorkerState) -> None:
        while True:
            d = q.get()
            if d is None:
                q.task_done()
                return
            try:
                if cancel is not None and cancel.is_set():
                    continue
                own = 0
                nfiles = 0
                try:
                    with os.scandir(d) as it:
                        for e in it:
                            try:
                                st = e.stat(follow_symlinks=False)
                            except OSError:
                                continue
                            if is_link_or_reparse(st):
                                continue
                            if stat.S_ISDIR(st.st_mode):
                                state.parent[e.path] = d
                                q.put(e.path)
                            else:
                                size = st.st_size
                                own += size
                                nfiles += 1
                                t = state.types.setdefault(file_type(e.name), [0, 0])
                                t[0] += size
                                t[1] += 1
                                if len(state.files) < top:
                                    heapq.heappush(state.files, (size, e.path))
                                elif size > state.files[0][0]:
                                    heapq.heapreplace(state.files, (size, e.path))
                except OSError as exc:
                    state.errors.append(f"{d}: {exc.strerror or exc}")
                state.own[d] = own
                state.file_count += nfiles
                with counter_lock:
                    counters["dirs"] += 1
                    counters["files"] += nfiles
                    counters["bytes"] += own
                    report = progress is not None and counters["dirs"] % 500 == 0
                    snapshot = (counters["dirs"], counters["files"], counters["bytes"])
                if report and progress is not None:
                    progress(*snapshot)
            finally:
                q.task_done()

    threads = [
        threading.Thread(target=work, args=(s,), daemon=True, name=f"analyze-{i}")
        for i, s in enumerate(states)
    ]
    for t in threads:
        t.start()
    q.join()
    for _ in threads:
        q.put(None)
    for t in threads:
        t.join()

    own: dict[str, int] = {}
    parent: dict[str, str] = {}
    files: list[tuple[int, str]] = []
    types: dict[str, list[int]] = {}
    result = AnalysisResult(root=root)
    for s in states:
        own.update(s.own)
        parent.update(s.parent)
        files.extend(s.files)
        result.file_count += s.file_count
        for ext, (b, c) in s.types.items():
            agg = types.setdefault(ext, [0, 0])
            agg[0] += b
            agg[1] += c
        result.errors.extend(s.errors)

    totals = dict(own)
    for d in sorted(own, key=lambda p: p.count(os.sep), reverse=True):
        par = parent.get(d)
        if par is not None and par in totals:
            totals[par] += totals[d]

    root_depth = root.rstrip(os.sep).count(os.sep)
    candidates = (
        (size, d)
        for d, size in totals.items()
        if d != root
        and (max_depth is None or d.rstrip(os.sep).count(os.sep) - root_depth <= max_depth)
    )
    result.top_dirs = [Entry(p, b) for b, p in heapq.nlargest(top, candidates)]
    result.top_files = [Entry(p, b) for b, p in heapq.nlargest(top, files)]
    result.by_type = top_types(types, n=type_count)
    kids = ((totals[d], d) for d, par in parent.items() if par == root and d in totals)
    result.children = [Entry(p, b) for b, p in heapq.nlargest(top, kids)]
    result.root_files_bytes = own.get(root, 0)
    result.total_bytes = totals.get(root, 0)
    result.dir_count = max(0, len(own) - 1)
    result.elapsed = time.perf_counter() - started
    if progress:
        progress(len(own), result.file_count, result.total_bytes)
    return result


def export_json(result: AnalysisResult, path: str | Path) -> None:
    """Write the analysis as JSON."""
    Path(path).write_text(json.dumps(result.to_dict(), indent=2), encoding="utf-8")


def export_csv(result: AnalysisResult, path: str | Path) -> None:
    """Write the analysis as CSV with columns ``kind,path,bytes``."""
    with Path(path).open("w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow(["kind", "path", "bytes"])
        for e in result.top_dirs:
            writer.writerow(["folder", e.path, e.bytes])
        for e in result.top_files:
            writer.writerow(["file", e.path, e.bytes])
        for t in result.by_type:
            writer.writerow(["type", t.ext, t.bytes])
