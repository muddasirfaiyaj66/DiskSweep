"""Duplicate file finder.

Three-stage pipeline, each stage only touching files that survived the previous:

1. group by exact size (free: comes from the directory walk);
2. group by a *partial* BLAKE2b hash of the first and last 64 KiB;
3. group by a *full* BLAKE2b hash.

Hard links to the same file are recognised and not reported as duplicates.
Removing duplicates always sends them to the Recycle Bin and goes through the
:class:`~disksweep.safety.PathGuard`.
"""

from __future__ import annotations

import hashlib
import os
import threading
from collections import defaultdict
from collections.abc import Callable, Iterable, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass

from disksweep.audit import AuditLog
from disksweep.fsops import Deleter, iter_files
from disksweep.models import CleanResult, DeleteMode
from disksweep.safety import PathGuard

PARTIAL_CHUNK = 64 * 1024
FULL_CHUNK = 1024 * 1024


@dataclass
class DuplicateGroup:
    """Files with identical content."""

    size: int
    digest: str
    files: list[str]

    @property
    def wasted(self) -> int:
        """Bytes that would be freed by keeping only one copy."""
        return self.size * (len(self.files) - 1)

    def to_dict(self) -> dict[str, object]:
        """JSON-serialisable dict."""
        return {**asdict(self), "wasted": self.wasted}


def partial_hash(path: str, size: int) -> str:
    """BLAKE2b of the first and last :data:`PARTIAL_CHUNK` bytes (plus the size)."""
    h = hashlib.blake2b(digest_size=20)
    h.update(size.to_bytes(8, "little"))
    with open(path, "rb") as fh:
        h.update(fh.read(PARTIAL_CHUNK))
        if size > 2 * PARTIAL_CHUNK:
            fh.seek(-PARTIAL_CHUNK, os.SEEK_END)
            h.update(fh.read(PARTIAL_CHUNK))
    return h.hexdigest()


def full_hash(path: str) -> str:
    """BLAKE2b of the whole file."""
    h = hashlib.blake2b(digest_size=32)
    with open(path, "rb") as fh:
        while chunk := fh.read(FULL_CHUNK):
            h.update(chunk)
    return h.hexdigest()


def _group_by(
    items: Iterable[str], key: Callable[[str], str | None], workers: int
) -> dict[str, list[str]]:
    groups: dict[str, list[str]] = defaultdict(list)
    items = list(items)

    def safe_key(p: str) -> str | None:
        try:
            return key(p)
        except OSError:
            return None  # unreadable / locked: skip

    with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="hash") as pool:
        for path, k in zip(items, pool.map(safe_key, items), strict=True):
            if k is not None:
                groups[k].append(path)
    return groups


def _drop_hardlinks(paths: list[str]) -> list[str]:
    seen: set[tuple[int, int]] = set()
    out: list[str] = []
    for p in paths:
        try:
            st = os.stat(p)
        except OSError:
            continue
        ident = (st.st_dev, st.st_ino)
        if st.st_ino and ident in seen:
            continue
        seen.add(ident)
        out.append(p)
    return out


def find_duplicates(
    roots: Sequence[str],
    min_size: int = 1,
    *,
    workers: int = 8,
    cancel: threading.Event | None = None,
) -> list[DuplicateGroup]:
    """Find groups of identical files under ``roots``, largest waste first.

    Args:
        roots: folders to search.
        min_size: ignore files smaller than this many bytes (empty files are always ignored).
        workers: hashing threads.
        cancel: event that stops the search early.
    """
    by_size: dict[int, list[str]] = defaultdict(list)
    seen_paths: set[str] = set()
    for root in roots:
        for path, st in iter_files(os.path.abspath(root), cancel):
            size = st.st_size
            if size < max(1, min_size):
                continue
            key = os.path.normcase(path)
            if key in seen_paths:
                continue
            seen_paths.add(key)
            by_size[size].append(path)

    groups: list[DuplicateGroup] = []
    for size, paths in by_size.items():
        if cancel is not None and cancel.is_set():
            break
        if len(paths) < 2:
            continue
        paths = _drop_hardlinks(paths)
        if len(paths) < 2:
            continue
        partial = _group_by(paths, lambda p, s=size: partial_hash(p, s), workers)
        for partial_digest, same_partial in partial.items():
            if len(same_partial) < 2:
                continue
            if size <= 2 * PARTIAL_CHUNK:
                # The partial hash already covered the whole file.
                full = {partial_digest: same_partial}
            else:
                full = _group_by(same_partial, full_hash, workers)
            for digest, same in full.items():
                if len(same) > 1:
                    groups.append(DuplicateGroup(size, digest, sorted(same)))
    groups.sort(key=lambda g: g.wasted, reverse=True)
    return groups


def choose_keeper(files: Sequence[str], keep: str = "oldest") -> str:
    """Pick the copy to keep: ``oldest``, ``newest``, ``shortest`` path, or ``first``."""
    if keep == "first":
        return files[0]
    if keep == "shortest":
        return min(files, key=lambda p: (len(p), p))

    def mtime(p: str) -> float:
        try:
            return os.stat(p).st_mtime
        except OSError:
            return float("inf") if keep == "oldest" else float("-inf")

    return min(files, key=mtime) if keep == "oldest" else max(files, key=mtime)


def remove_duplicates(
    groups: Sequence[DuplicateGroup],
    guard: PathGuard,
    *,
    keep: str = "oldest",
    dry_run: bool = True,
    audit: AuditLog | None = None,
) -> CleanResult:
    """Send all but one file of each group to the Recycle Bin.

    Every path is still checked by ``guard``: duplicates inside protected folders
    (e.g. Documents) are reported as skipped, never removed.
    """
    deleter = Deleter(
        guard, dry_run=dry_run, mode=DeleteMode.RECYCLE, audit=audit, target_id="duplicates"
    )
    for group in groups:
        keeper = choose_keeper(group.files, keep)
        for path in group.files:
            if path != keeper:
                deleter.delete_path(path)
    return deleter.result
