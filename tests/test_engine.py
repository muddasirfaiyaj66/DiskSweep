"""Engine: before/after free space, audit summary, error isolation."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from conftest import make_old, make_tree
from disksweep import osutils
from disksweep.audit import AuditLog
from disksweep.engine import clean_targets, scan_targets
from disksweep.models import CleanResult, ScanResult
from disksweep.targets.base import CleanupTarget, TargetContext
from disksweep.targets.system import UserTemp


class Exploding(CleanupTarget):
    id = "exploding"
    name = "Exploding"
    description = "raises"

    def scan(self, ctx: TargetContext) -> ScanResult:
        raise RuntimeError("scan boom")

    def clean(self, ctx: TargetContext, dry_run: bool = True) -> CleanResult:
        raise RuntimeError("clean boom")


def _fake_free(monkeypatch: pytest.MonkeyPatch, values: list[int]) -> None:
    it = iter(values)
    monkeypatch.setattr(osutils, "disk_free", lambda path: next(it))


def test_scan_isolates_failures(ctx: TargetContext) -> None:
    results = scan_targets([Exploding(), UserTemp()], ctx)
    assert list(results) == ["exploding", "user_temp"]
    assert not results["exploding"].available
    assert "scan boom" in results["exploding"].note


def test_clean_is_dry_run_unless_apply(ctx: TargetContext, monkeypatch: pytest.MonkeyPatch) -> None:
    _fake_free(monkeypatch, [100, 100])
    temp = Path(ctx.environ["TEMP"])
    make_tree(temp, {"a.tmp": b"x" * 10})
    make_old(temp / "a.tmp", 5)
    report = clean_targets([UserTemp()], ctx)
    assert report.dry_run
    assert report.total_bytes == 10
    assert (temp / "a.tmp").exists()


def test_clean_reports_free_space_and_audits(
    ctx: TargetContext, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _fake_free(monkeypatch, [1_000, 1_010])
    temp = Path(ctx.environ["TEMP"])
    make_tree(temp, {"a.tmp": b"x" * 10})
    make_old(temp / "a.tmp", 5)
    log = tmp_path / "audit.jsonl"
    with AuditLog(log) as audit:
        ctx.audit = audit
        report = clean_targets([UserTemp(), Exploding()], ctx, apply=True)
    assert not report.dry_run
    assert (report.free_before, report.free_after) == (1_000, 1_010)
    assert report.total_bytes == 10
    failed = report.results[1]
    assert not failed.ran and "clean boom" in failed.errors[0]

    events = [json.loads(line) for line in log.read_text().splitlines()]
    kinds = [e["event"] for e in events]
    assert kinds[0] == "run_start" and kinds[-1] == "run_summary"
    deletes = [e for e in events if e["event"] == "delete"]
    assert len(deletes) == 1 and deletes[0]["bytes"] == 10
    assert events[-1]["bytes_freed"] == 10
    assert events[-1]["free_after"] == 1_010


def test_cancel_marks_remaining(ctx: TargetContext, monkeypatch: pytest.MonkeyPatch) -> None:
    _fake_free(monkeypatch, [1, 1])
    ctx.cancel.set()
    report = clean_targets([UserTemp()], ctx, apply=True)
    assert report.results[0].note == "cancelled"
