"""Deletion engine: dry run, links, locked files, recycle mode, audit."""

from __future__ import annotations

import json
import os
import re
from pathlib import Path

import pytest

from conftest import make_old, make_tree, try_symlink
from disksweep import fsops
from disksweep.audit import AuditLog
from disksweep.fsops import Deleter
from disksweep.models import DeleteMode
from disksweep.safety import PathGuard

SRC = Path(__file__).resolve().parents[1] / "src" / "disksweep"


def test_dry_run_deletes_nothing(guard: PathGuard, tmp_path: Path) -> None:
    root = tmp_path / "cache"
    make_tree(root, {"a.bin": b"x" * 100, "sub/b.bin": b"y" * 50})
    res = Deleter(guard, dry_run=True).purge_contents(str(root))
    assert res.bytes_freed == 150
    assert res.files_deleted == 2
    assert (root / "a.bin").exists()
    assert (root / "sub" / "b.bin").exists()


def test_apply_purges_contents_keeps_root(guard: PathGuard, tmp_path: Path) -> None:
    root = tmp_path / "cache"
    make_tree(root, {"a.bin": b"x" * 100, "sub/deeper/b.bin": b"y" * 50})
    res = Deleter(guard, dry_run=False).purge_contents(str(root))
    assert res.bytes_freed == 150
    assert root.is_dir()
    assert list(root.iterdir()) == []


def test_symlink_inside_target_not_followed(guard: PathGuard, tmp_path: Path) -> None:
    root = tmp_path / "cache"
    outside = tmp_path / "outside"
    make_tree(root, {"junk.tmp": b"j"})
    make_tree(outside, {"precious.txt": b"keep me"})
    try_symlink(outside, root / "link", directory=True)

    dry = Deleter(guard, dry_run=True).purge_contents(str(root))
    assert dry.bytes_freed == 1  # the outside file is never counted

    Deleter(guard, dry_run=False).purge_contents(str(root))
    assert (outside / "precious.txt").read_text() == "keep me"
    assert not os.path.lexists(root / "link")  # link itself removed
    assert not (root / "junk.tmp").exists()


def test_file_symlink_not_followed(guard: PathGuard, tmp_path: Path) -> None:
    root = tmp_path / "cache"
    root.mkdir()
    target = tmp_path / "important.txt"
    target.write_text("data")
    try_symlink(target, root / "link.txt", directory=False)
    Deleter(guard, dry_run=False).purge_contents(str(root))
    assert target.read_text() == "data"
    assert not os.path.lexists(root / "link.txt")


def test_root_that_is_a_link_is_skipped(guard: PathGuard, tmp_path: Path) -> None:
    real = tmp_path / "real"
    make_tree(real, {"f": b"1"})
    try_symlink(real, tmp_path / "linkroot", directory=True)
    res = Deleter(guard, dry_run=False).purge_contents(str(tmp_path / "linkroot"))
    assert (real / "f").exists()
    assert res.skipped and "link" in res.skipped[0].reason


def test_protected_root_refused(guard: PathGuard, tmp_path: Path) -> None:
    protected = tmp_path / "protected"
    make_tree(protected, {"doc.txt": b"secret"})
    res = Deleter(guard, dry_run=False).purge_contents(str(protected))
    assert (protected / "doc.txt").exists()
    assert res.files_deleted == 0
    assert res.skipped


def test_delete_path_on_protected_file_refused(guard: PathGuard, tmp_path: Path) -> None:
    f = tmp_path / "protected" / "x.txt"
    f.write_text("x")
    res = Deleter(guard, dry_run=False).delete_path(str(f))
    assert f.exists()
    assert res.skipped


def test_locked_file_reported_not_raised(
    guard: PathGuard, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "cache"
    make_tree(root, {"locked.db": b"12345", "free.tmp": b"12"})
    real_unlink = os.unlink

    def fake_unlink(path: str, *a: object, **k: object) -> None:
        if str(path).endswith("locked.db"):
            raise PermissionError(13, "The process cannot access the file")
        real_unlink(path)

    monkeypatch.setattr(fsops.os, "unlink", fake_unlink)
    monkeypatch.setattr(fsops.os, "chmod", lambda *a, **k: None)
    res = Deleter(guard, dry_run=False).purge_contents(str(root))
    assert res.files_deleted == 1
    assert res.bytes_freed == 2
    assert len(res.skipped) == 1
    assert res.skipped[0].path.endswith("locked.db")
    assert "in use" in res.skipped[0].reason
    assert (root / "locked.db").exists()


@pytest.mark.skipif(os.name != "nt", reason="Windows file-locking semantics")
def test_really_open_file_is_skipped_on_windows(guard: PathGuard, tmp_path: Path) -> None:
    root = tmp_path / "cache"
    make_tree(root, {"open.log": b"abc"})
    with open(root / "open.log", "rb"):
        res = Deleter(guard, dry_run=False).purge_contents(str(root))
    assert res.skipped
    assert (root / "open.log").exists()


def test_read_only_file_is_deleted(guard: PathGuard, tmp_path: Path) -> None:
    root = tmp_path / "cache"
    make_tree(root, {"ro.bin": b"x"})
    os.chmod(root / "ro.bin", 0o444)
    res = Deleter(guard, dry_run=False).purge_contents(str(root))
    assert res.files_deleted == 1
    assert not (root / "ro.bin").exists()


def test_min_age_keeps_recent_files(guard: PathGuard, tmp_path: Path) -> None:
    root = tmp_path / "temp"
    make_tree(root, {"old.tmp": b"o" * 10, "new.tmp": b"n" * 5})
    make_old(root / "old.tmp", days=3)
    res = Deleter(guard, dry_run=False, min_age_seconds=86400).purge_contents(str(root))
    assert not (root / "old.tmp").exists()
    assert (root / "new.tmp").exists()
    assert res.bytes_freed == 10


def test_patterns_filter(guard: PathGuard, tmp_path: Path) -> None:
    root = tmp_path / "explorer"
    make_tree(root, {"thumbcache_96.db": b"t", "other.db": b"o"})
    Deleter(guard, dry_run=False, patterns=["thumbcache_*.db"]).purge_contents(str(root))
    assert not (root / "thumbcache_96.db").exists()
    assert (root / "other.db").exists()


def test_recycle_mode_uses_send2trash(
    guard: PathGuard, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[str] = []
    monkeypatch.setattr(fsops, "_send2trash", calls.append)
    f = tmp_path / "user" / "dup.iso"
    make_tree(tmp_path / "user", {"dup.iso": b"z" * 7})
    res = Deleter(guard, dry_run=False, mode=DeleteMode.RECYCLE).delete_path(str(f))
    assert calls == [str(f)]
    assert res.bytes_freed == 7


def test_direct_mode_never_uses_send2trash(
    guard: PathGuard, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(fsops, "_send2trash", lambda p: pytest.fail("recycled a cache file"))
    root = tmp_path / "cache"
    make_tree(root, {"a": b"a"})
    Deleter(guard, dry_run=False).purge_contents(str(root))
    assert not (root / "a").exists()


def test_delete_tree_including_root(guard: PathGuard, tmp_path: Path) -> None:
    old = tmp_path / "Windows.old"
    make_tree(old, {"Windows/a.dll": b"1234", "Users/x": b"5"})
    res = Deleter(guard, dry_run=False).delete_path(str(old), within=str(old))
    assert not old.exists()
    assert res.bytes_freed == 5


def test_audit_log_records_each_deletion(guard: PathGuard, tmp_path: Path) -> None:
    root = tmp_path / "cache"
    make_tree(root, {"a.bin": b"x" * 3, "b.bin": b"y" * 4})
    log_path = tmp_path / "audit.jsonl"
    with AuditLog(log_path) as audit:
        Deleter(guard, dry_run=False, audit=audit, target_id="t1").purge_contents(str(root))
    records = [json.loads(line) for line in log_path.read_text().splitlines()]
    deletes = {Path(r["path"]).name: r for r in records if r["event"] == "delete"}
    assert set(deletes) == {"a.bin", "b.bin"}
    assert deletes["a.bin"]["bytes"] == 3
    assert deletes["b.bin"]["target"] == "t1"
    assert deletes["b.bin"]["mode"] == "direct"


def test_dry_run_writes_no_audit(guard: PathGuard, tmp_path: Path) -> None:
    root = tmp_path / "cache"
    make_tree(root, {"a.bin": b"x"})
    log_path = tmp_path / "audit.jsonl"
    with AuditLog(log_path) as audit:
        Deleter(guard, dry_run=True, audit=audit).purge_contents(str(root))
    assert not log_path.exists()


def test_cancel_stops_walk(guard: PathGuard, tmp_path: Path) -> None:
    import threading

    root = tmp_path / "cache"
    make_tree(root, {f"f{i}": b"x" for i in range(20)})
    ev = threading.Event()
    ev.set()
    res = Deleter(guard, dry_run=False, cancel=ev).purge_contents(str(root))
    assert res.files_deleted == 0
    assert len(list(root.iterdir())) == 20


def test_only_fsops_deletes_files() -> None:
    """Structural guarantee: no other module calls a deletion primitive directly."""
    forbidden = re.compile(
        r"\b(os\.remove|os\.unlink|os\.rmdir|shutil\.rmtree|send2trash\.send2trash|"
        r"\.unlink\(|\.rmdir\(|os\.removedirs)"
    )
    offenders = []
    for path in SRC.rglob("*.py"):
        if path.name == "fsops.py":
            continue
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if forbidden.search(line) and not line.lstrip().startswith("#"):
                offenders.append(f"{path.relative_to(SRC)}:{lineno}: {line.strip()}")
    assert offenders == []
