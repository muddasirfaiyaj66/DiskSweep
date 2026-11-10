"""Duplicate finder."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from conftest import make_tree
from disksweep import duplicates, fsops
from disksweep.duplicates import (
    PARTIAL_CHUNK,
    choose_keeper,
    find_duplicates,
    remove_duplicates,
)
from disksweep.safety import PathGuard


def test_finds_identical_files(tmp_path: Path) -> None:
    make_tree(
        tmp_path / "d",
        {
            "a.txt": b"same content",
            "sub/b.txt": b"same content",
            "c.txt": b"different!!!",  # same size, different content
            "lonely.txt": b"unique size here",
            "empty1": b"",
            "empty2": b"",
        },
    )
    groups = find_duplicates([str(tmp_path / "d")])
    assert len(groups) == 1
    names = sorted(Path(f).name for f in groups[0].files)
    assert names == ["a.txt", "b.txt"]
    assert groups[0].wasted == len(b"same content")


def test_large_files_same_head_and_tail_differ_in_middle(tmp_path: Path) -> None:
    head = b"H" * PARTIAL_CHUNK
    tail = b"T" * PARTIAL_CHUNK
    make_tree(
        tmp_path,
        {
            "x.bin": head + b"A" * 1000 + tail,
            "y.bin": head + b"B" * 1000 + tail,
            "z.bin": head + b"A" * 1000 + tail,
        },
    )
    groups = find_duplicates([str(tmp_path)])
    assert len(groups) == 1
    assert sorted(Path(f).name for f in groups[0].files) == ["x.bin", "z.bin"]


def test_min_size(tmp_path: Path) -> None:
    make_tree(tmp_path, {"a": b"x" * 10, "b": b"x" * 10})
    assert find_duplicates([str(tmp_path)], min_size=11) == []
    assert len(find_duplicates([str(tmp_path)], min_size=10)) == 1


def test_hardlinks_not_reported(tmp_path: Path) -> None:
    make_tree(tmp_path, {"a": b"hardlinked data"})
    try:
        os.link(tmp_path / "a", tmp_path / "b")
    except OSError:
        pytest.skip("hard links unsupported")
    assert find_duplicates([str(tmp_path)]) == []


def test_overlapping_roots_not_double_counted(tmp_path: Path) -> None:
    make_tree(tmp_path, {"sub/a": b"q" * 5, "sub/b": b"q" * 5})
    groups = find_duplicates([str(tmp_path), str(tmp_path / "sub")])
    assert len(groups) == 1 and len(groups[0].files) == 2


def test_choose_keeper(tmp_path: Path) -> None:
    make_tree(tmp_path, {"long/name/a": b"1", "b": b"1"})
    old, new = tmp_path / "long" / "name" / "a", tmp_path / "b"
    os.utime(old, (1_000_000, 1_000_000))
    files = [str(old), str(new)]
    assert choose_keeper(files, "oldest") == str(old)
    assert choose_keeper(files, "newest") == str(new)
    assert choose_keeper(files, "shortest") == str(new)
    assert choose_keeper(files, "first") == str(old)


def test_remove_duplicates_recycles_and_respects_guard(
    guard: PathGuard, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    recycled: list[str] = []
    monkeypatch.setattr(fsops, "_send2trash", recycled.append)
    make_tree(
        tmp_path,
        {"dl/a.iso": b"z" * 20, "dl/b.iso": b"z" * 20, "protected/c.iso": b"z" * 20},
    )
    os.utime(tmp_path / "dl" / "a.iso", (1_000_000, 1_000_000))  # keeper
    groups = find_duplicates([str(tmp_path / "dl"), str(tmp_path / "protected")])
    dry = remove_duplicates(groups, guard, keep="oldest", dry_run=True)
    assert recycled == [] and dry.files_deleted == 1
    res = remove_duplicates(groups, guard, keep="oldest", dry_run=False)
    assert [Path(p).name for p in recycled] == ["b.iso"]
    assert res.bytes_freed == 20
    assert any("c.iso" in s.path for s in res.skipped)  # protected copy never touched


def test_unreadable_files_skipped(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    make_tree(tmp_path, {"a": b"k" * 5, "b": b"k" * 5})
    real = duplicates.partial_hash

    def flaky(path: str, size: int) -> str:
        if path.endswith("b"):
            raise PermissionError("locked")
        return real(path, size)

    monkeypatch.setattr(duplicates, "partial_hash", flaky)
    assert find_duplicates([str(tmp_path)]) == []
