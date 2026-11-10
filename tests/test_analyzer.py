"""Disk analyzer."""

from __future__ import annotations

import csv
import json
import threading
from pathlib import Path

import pytest

from conftest import make_tree, try_symlink
from disksweep.analyzer import analyze, export_csv, export_json


@pytest.fixture
def tree(tmp_path: Path) -> Path:
    root = tmp_path / "data"
    make_tree(
        root,
        {
            "big/a.bin": b"a" * 5000,
            "big/nested/b.bin": b"b" * 3000,
            "small/c.txt": b"c" * 10,
            "top.bin": b"t" * 100,
        },
    )
    return root


def test_sizes_and_ranking(tree: Path) -> None:
    res = analyze(str(tree), top=10, workers=4)
    assert res.total_bytes == 8110
    assert res.file_count == 4
    assert res.dir_count == 3
    sizes = {Path(e.path).name: e.bytes for e in res.top_dirs}
    assert sizes == {"big": 8000, "nested": 3000, "small": 10}
    assert Path(res.top_dirs[0].path).name == "big"
    assert [Path(e.path).name for e in res.top_files][:2] == ["a.bin", "b.bin"]


def test_top_limit_and_depth(tree: Path) -> None:
    res = analyze(str(tree), top=1)
    assert len(res.top_dirs) == 1 and len(res.top_files) == 1
    shallow = analyze(str(tree), top=10, max_depth=1)
    assert {Path(e.path).name for e in shallow.top_dirs} == {"big", "small"}


def test_symlinks_not_followed(tree: Path, tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    make_tree(outside, {"huge.bin": b"h" * 50_000})
    try_symlink(outside, tree / "link", directory=True)
    res = analyze(str(tree))
    assert res.total_bytes == 8110


def test_cancel_returns_partial(tree: Path) -> None:
    ev = threading.Event()
    ev.set()
    res = analyze(str(tree), cancel=ev)
    assert res.total_bytes == 0


def test_not_a_directory(tmp_path: Path) -> None:
    with pytest.raises(NotADirectoryError):
        analyze(str(tmp_path / "missing"))


def test_export(tree: Path, tmp_path: Path) -> None:
    res = analyze(str(tree), top=5)
    export_json(res, tmp_path / "r.json")
    export_csv(res, tmp_path / "r.csv")
    data = json.loads((tmp_path / "r.json").read_text())
    assert data["total_bytes"] == 8110
    with (tmp_path / "r.csv").open(newline="") as fh:
        rows = list(csv.reader(fh))
    assert rows[0] == ["kind", "path", "bytes"]
    assert {r[0] for r in rows[1:]} == {"folder", "file", "type"}


def test_space_by_file_type(tree: Path) -> None:
    res = analyze(str(tree))
    by = {t.ext: (t.bytes, t.files) for t in res.by_type}
    assert by == {".bin": (8100, 3), ".txt": (10, 1)}
    assert sum(t.bytes for t in res.by_type) == res.total_bytes


def test_top_types_folds_remainder_into_other() -> None:
    from disksweep.analyzer import OTHER_TYPES, top_types

    totals = {f".e{i}": [100 - i, 1] for i in range(12)}
    out = top_types(totals, n=8)
    assert len(out) == 9 and out[-1].ext == OTHER_TYPES
    assert out[-1].bytes == sum(100 - i for i in range(8, 12)) and out[-1].files == 4
    assert [t.ext for t in out[:2]] == [".e0", ".e1"]


def test_file_type_labels() -> None:
    from disksweep.analyzer import NO_EXTENSION, file_type

    assert file_type("Movie.MKV") == ".mkv"
    assert file_type("Makefile") == NO_EXTENSION
    assert file_type(".bashrc") == NO_EXTENSION


def test_direct_children_for_drill_down(tree: Path) -> None:
    res = analyze(str(tree))
    assert [(Path(e.path).name, e.bytes) for e in res.children] == [("big", 8000), ("small", 10)]
    assert res.root_files_bytes == 100
