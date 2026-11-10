"""Filesystem operations. **The only module in DiskSweep that deletes anything.**

Every removal goes through :class:`Deleter`, which

* asks :class:`~disksweep.safety.PathGuard` about *every* path before touching it;
* walks directories with ``lstat``/``scandir`` and never follows symlinks or
  junctions (a link is removed as a link, its target is never entered);
* treats locked / in-use / access-denied files as *skipped* and reports them
  instead of raising;
* in dry-run mode performs exactly the same walk and checks but removes nothing,
  so a dry run reports what a real run would free;
* records every real deletion in the :class:`~disksweep.audit.AuditLog`.
"""

from __future__ import annotations

import fnmatch
import logging
import os
import stat
import sys
import threading
import time
from collections.abc import Iterable

import send2trash

from disksweep.audit import AuditLog
from disksweep.models import CleanResult, DeleteMode, SkippedItem
from disksweep.safety import PathGuard, ProtectedPathError, is_link_or_reparse

log = logging.getLogger(__name__)

_FILE_ATTRIBUTE_DIRECTORY = 0x10
_IS_WINDOWS = sys.platform == "win32"


def _send2trash(path: str) -> None:
    """Move ``path`` to the Recycle Bin (indirection point for tests)."""
    send2trash.send2trash(path)


class Deleter:
    """Guarded, link-safe, locked-file-tolerant deletion.

    Args:
        guard: the path guard consulted before every removal.
        dry_run: if True (the default) nothing is removed.
        mode: :attr:`DeleteMode.DIRECT` (permanent) or :attr:`DeleteMode.RECYCLE`.
        audit: audit log receiving one record per deleted path.
        target_id: cleanup-target id used in results and audit records.
        cancel: event that aborts the walk when set.
        min_age_seconds: only delete files (and empty folders) older than this.
        patterns: only delete files whose name matches one of these globs.
    """

    def __init__(
        self,
        guard: PathGuard,
        *,
        dry_run: bool = True,
        mode: DeleteMode = DeleteMode.DIRECT,
        audit: AuditLog | None = None,
        target_id: str = "",
        cancel: threading.Event | None = None,
        min_age_seconds: float | None = None,
        patterns: Iterable[str] | None = None,
    ) -> None:
        self.guard = guard
        self.dry_run = dry_run
        self.mode = mode
        self.audit = audit
        self.cancel = cancel
        self.min_age_seconds = min_age_seconds
        self.patterns = tuple(p.lower() for p in patterns) if patterns else ()
        self.now = time.time()
        self.result = CleanResult(target_id=target_id, dry_run=dry_run)

    # ------------------------------------------------------------------ #
    # public API
    # ------------------------------------------------------------------ #

    def purge_contents(self, root: str) -> CleanResult:
        """Delete everything inside ``root`` (honouring filters); keep ``root`` itself."""
        root = os.path.abspath(root)
        if not self._root_ok(root):
            return self.result
        self._purge_dir(root, root)
        return self.result

    def delete_path(self, path: str, within: str | None = None) -> CleanResult:
        """Delete a single file, link, or whole directory tree (including ``path``).

        Args:
            path: what to delete.
            within: root the path must stay inside (defaults to the parent folder).
        """
        path = os.path.abspath(path)
        within = os.path.abspath(within) if within else os.path.dirname(path)
        try:
            st = os.lstat(path)
        except FileNotFoundError:
            return self.result
        except OSError as exc:
            self._skip(path, f"cannot stat: {exc.strerror or exc}")
            return self.result

        if is_link_or_reparse(st):
            self._delete_link(path, within, st)
        elif stat.S_ISDIR(st.st_mode):
            self._delete_tree(path, within)
        else:
            self._delete_file(path, within, st.st_size)
        return self.result

    # ------------------------------------------------------------------ #
    # internals
    # ------------------------------------------------------------------ #

    def _cancelled(self) -> bool:
        return self.cancel is not None and self.cancel.is_set()

    def _skip(self, path: str, reason: str) -> None:
        self.result.skipped.append(SkippedItem(path, reason))
        log.debug("skipped", extra={"path": path, "reason": reason})

    def _root_ok(self, root: str) -> bool:
        if not os.path.isdir(root) and not os.path.islink(root):
            return False
        if is_link_or_reparse(os.lstat(root)):
            self._skip(root, "folder is a symlink/junction; not followed")
            return False
        try:
            # Probe: may *children* of this root be deleted at all?
            self.guard.check(os.path.join(root, "__disksweep_probe__"), root, link=True)
        except ProtectedPathError as exc:
            self._skip(root, str(exc))
            return False
        return True

    def _old_enough(self, st: os.stat_result) -> bool:
        if not self.min_age_seconds:
            return True
        # On Windows st_ctime is the *creation* time: a file just extracted with an
        # old mtime is still treated as new (conservative). On POSIX st_ctime is the
        # inode-change time, which is not meaningful here, so only mtime is used.
        newest = max(st.st_mtime, st.st_ctime) if _IS_WINDOWS else st.st_mtime
        return (self.now - newest) >= self.min_age_seconds

    def _matches(self, name: str) -> bool:
        if not self.patterns:
            return True
        lname = name.lower()
        return any(fnmatch.fnmatchcase(lname, p) for p in self.patterns)

    def _purge_dir(self, directory: str, root: str) -> None:
        try:
            with os.scandir(directory) as it:
                entries = list(it)
        except OSError as exc:
            self._skip(directory, f"cannot list: {exc.strerror or exc}")
            return
        for entry in entries:
            if self._cancelled():
                return
            try:
                st = entry.stat(follow_symlinks=False)
            except OSError as exc:
                self._skip(entry.path, f"cannot stat: {exc.strerror or exc}")
                continue
            if is_link_or_reparse(st):
                if self._matches(entry.name):
                    self._delete_link(entry.path, root, st)
            elif stat.S_ISDIR(st.st_mode):
                self._purge_dir(entry.path, root)
                if not self.patterns and self._old_enough(st):
                    self._remove_empty_dir(entry.path, root)
            elif self._matches(entry.name) and self._old_enough(st):
                self._delete_file(entry.path, root, st.st_size)

    def _check(self, path: str, within: str, *, link: bool = True) -> bool:
        try:
            self.guard.check(path, within, link=link)
        except ProtectedPathError as exc:
            self._skip(path, str(exc))
            return False
        return True

    def _record(self, path: str, size: int, *, is_dir: bool = False) -> None:
        if is_dir:
            self.result.dirs_deleted += 1
        else:
            self.result.files_deleted += 1
        self.result.bytes_freed += size
        if not self.dry_run and self.audit is not None:
            self.audit.record_delete(self.result.target_id, path, size, self.mode.value)

    def _unlink_file(self, path: str) -> None:
        if self.mode is DeleteMode.RECYCLE:
            _send2trash(path)
            return
        try:
            os.unlink(path)
        except PermissionError:
            # Read-only attribute blocks deletion on Windows: clear it and retry once.
            st = os.lstat(path)
            if st.st_mode & stat.S_IWRITE:
                raise
            os.chmod(path, stat.S_IWRITE | stat.S_IREAD)
            os.unlink(path)

    def _delete_file(self, path: str, within: str, size: int) -> None:
        # ``link=True``: lstat already showed this is a regular file, so only its
        # parent needs resolving.
        if not self._check(path, within):
            return
        if not self.dry_run:
            try:
                self._unlink_file(path)
            except FileNotFoundError:
                return
            except OSError as exc:
                self._skip(path, f"in use or access denied: {exc.strerror or exc}")
                return
        self._record(path, size)

    def _delete_link(self, path: str, within: str, st: os.stat_result) -> None:
        """Remove a symlink/junction itself. Its target is never entered."""
        if not self._check(path, within, link=True):
            return
        if not self.dry_run:
            try:
                is_dir_like = stat.S_ISDIR(st.st_mode) or bool(
                    getattr(st, "st_file_attributes", 0) & _FILE_ATTRIBUTE_DIRECTORY
                )
                if _IS_WINDOWS and is_dir_like:
                    os.rmdir(path)  # removes a junction / dir symlink, not its target
                else:
                    os.unlink(path)
            except FileNotFoundError:
                return
            except OSError as exc:
                self._skip(path, f"cannot remove link: {exc.strerror or exc}")
                return
        self._record(path, 0)

    def _remove_empty_dir(self, path: str, within: str) -> None:
        if self.dry_run:
            return  # emptiness unknown without deleting; folders carry no size
        try:
            self.guard.check(path, within, link=True)
        except ProtectedPathError as exc:
            self._skip(path, str(exc))
            return
        try:
            os.rmdir(path)
        except OSError:
            return  # not empty (filtered or locked content) - fine
        self._record(path, 0, is_dir=True)

    def _tree_size(self, path: str) -> tuple[int, int]:
        total = files = 0
        stack = [path]
        while stack:
            d = stack.pop()
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
                            stack.append(e.path)
                        else:
                            total += st.st_size
                            files += 1
            except OSError:
                continue
        return total, files

    def _delete_tree(self, path: str, within: str) -> None:
        if not self._check(path, within, link=False):
            return
        if self.mode is DeleteMode.RECYCLE:
            size, files = self._tree_size(path)
            if not self.dry_run:
                try:
                    _send2trash(path)
                except OSError as exc:
                    self._skip(path, f"cannot recycle: {exc}")
                    return
            self.result.files_deleted += files - 1 if files else 0
            self._record(path, size)
            return
        self._purge_dir(path, within)
        if not self.dry_run and not self._cancelled():
            try:
                os.rmdir(path)
                self._record(path, 0, is_dir=True)
            except OSError:
                pass


def iter_files(
    root: str, cancel: threading.Event | None = None
) -> Iterable[tuple[str, os.stat_result]]:
    """Yield ``(path, lstat)`` for regular files under ``root`` without following links."""
    stack = [root]
    while stack:
        if cancel is not None and cancel.is_set():
            return
        d = stack.pop()
        try:
            with os.scandir(d) as it:
                entries = list(it)
        except OSError:
            continue
        for e in entries:
            try:
                st = e.stat(follow_symlinks=False)
            except OSError:
                continue
            if is_link_or_reparse(st):
                continue
            if stat.S_ISDIR(st.st_mode):
                stack.append(e.path)
            elif stat.S_ISREG(st.st_mode):
                yield e.path, st
