"""Command-line interface."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from conftest import make_old, make_tree
from disksweep import cli, osutils
from disksweep.audit import tail


@pytest.fixture
def fake_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """Point every environment variable used by targets into tmp."""
    base = tmp_path / "env"
    env = {
        "TEMP": str(base / "Temp"),
        "TMP": str(base / "Temp"),
        "LOCALAPPDATA": str(base / "Local"),
        "APPDATA": str(base / "Roaming"),
        "USERPROFILE": str(base / "Profile"),
        "SystemRoot": str(base / "Windows"),
        "SystemDrive": str(base / "Drive"),
        "ProgramData": str(base / "ProgramData"),
        "DISKSWEEP_HOME": str(tmp_path / "home"),
        "HOME": str(tmp_path / "realhome"),
    }
    monkeypatch.setattr(osutils, "env", lambda: env)
    monkeypatch.setattr(osutils, "disk_free", lambda path: 10 * 1024**3)
    monkeypatch.setattr(osutils, "IS_WINDOWS", False)  # no external tools
    temp = base / "Temp"
    make_tree(temp, {"old.tmp": b"x" * 2048})
    make_old(temp / "old.tmp", 3)
    return temp


def test_no_command_prints_help(capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main([]) == cli.EXIT_USAGE
    assert "usage" in capsys.readouterr().out


def test_targets_json(capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main(["targets", "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert any(t["id"] == "user_temp" and t["risk"] == "safe" for t in data)


def test_scan_is_read_only(fake_env: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main(["scan", "--target", "user_temp"]) == 0
    out = capsys.readouterr().out
    assert "user_temp" in out and "2.0 KB" in out
    assert (fake_env / "old.tmp").exists()


def test_clean_without_apply_is_dry_run(fake_env: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main(["clean", "--profile", "safe"]) == 0
    out = capsys.readouterr().out
    assert "DRY RUN" in out
    assert (fake_env / "old.tmp").exists()
    assert tail() == []  # nothing audited


def test_clean_apply_deletes_and_audits(fake_env: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main(["clean", "--target", "user_temp", "--apply"]) == 0
    out = capsys.readouterr().out
    assert "FREED" in out and "Audit log" in out
    assert not (fake_env / "old.tmp").exists()
    events = [json.loads(line) for line in tail()]
    assert any(e["event"] == "delete" and e["path"].endswith("old.tmp") for e in events)


def test_clean_json(fake_env: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main(["clean", "--target", "user_temp", "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["dry_run"] is True and data["total_bytes"] == 2048


def test_non_safe_apply_requires_yes_when_non_interactive(
    fake_env: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)
    assert cli.main(["clean", "--target", "maven_repo", "--apply"]) == cli.EXIT_USAGE
    assert "--yes" in capsys.readouterr().err


def test_interactive_confirmation_declined(
    fake_env: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    monkeypatch.setattr("builtins.input", lambda prompt: "n")
    assert cli.main(["clean", "--target", "gradle_cache", "--apply"]) == 0
    assert "Aborted" in capsys.readouterr().out


def test_unknown_target_rejected(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exc:
        cli.main(["clean", "--target", "system32"])
    assert exc.value.code == 2


def test_analyze_with_exports(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    make_tree(tmp_path / "d", {"a/big.bin": b"b" * 4000, "small.txt": b"s"})
    j, c = tmp_path / "o.json", tmp_path / "o.csv"
    rc = cli.main(["analyze", str(tmp_path / "d"), "--top", "5", "--json", str(j), "--csv", str(c)])
    assert rc == 0
    assert "big.bin" in capsys.readouterr().out
    assert json.loads(j.read_text())["total_bytes"] == 4001
    assert c.read_text().startswith("kind,path,bytes")


def test_analyze_missing_folder(tmp_path: Path) -> None:
    assert cli.main(["analyze", str(tmp_path / "nope")]) == cli.EXIT_USAGE


def test_duplicates_report_and_dry_run_remove(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    make_tree(tmp_path / "d", {"a.bin": b"z" * 2048, "b.bin": b"z" * 2048})
    rc = cli.main(["duplicates", str(tmp_path / "d"), "--min-size", "1KB", "--remove"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "1 duplicate groups" in out and "DRY RUN" in out
    assert (tmp_path / "d" / "a.bin").exists() and (tmp_path / "d" / "b.bin").exists()


def test_large(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    make_tree(tmp_path, {"huge.bin": b"h" * 5000})
    assert cli.main(["large", str(tmp_path), "--min-size", "1KB"]) == 0
    assert "huge.bin" in capsys.readouterr().out


def test_schedule_dry_run(capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main(["schedule", "--weekly", "--profile", "safe", "--dry-run"]) == 0
    out = capsys.readouterr().out
    assert "schtasks /Create" in out and "WEEKLY" in out


def test_schedule_executes_schtasks(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    ran: list[list[str]] = []
    monkeypatch.setattr(osutils, "IS_WINDOWS", True)
    monkeypatch.setattr(
        osutils,
        "run_cmd",
        lambda args, timeout=0, input_text=None: (
            ran.append(list(args)) or osutils.CommandResult(tuple(args), 0, "SUCCESS", "")
        ),
    )
    assert cli.main(["schedule", "--daily", "--time", "02:15"]) == 0
    assert ran and ran[0][:2] == ["schtasks", "/Create"]
    assert cli.main(["schedule", "--remove"]) == 0
    assert ran[1][:2] == ["schtasks", "/Delete"]


def test_log_empty(capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main(["log"]) == 0
    assert "No audit entries" in capsys.readouterr().out


def test_version(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit):
        cli.main(["--version"])
    assert "disksweep" in capsys.readouterr().out
