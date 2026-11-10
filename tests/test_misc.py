"""Units, profiles, scheduler, large files, update check, logging."""

from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path

import pytest

from conftest import make_tree
from disksweep import updatecheck
from disksweep.largefiles import find_large_files
from disksweep.logging_setup import JsonFormatter, KeyValueFormatter
from disksweep.models import Risk
from disksweep.profiles import AGGRESSIVE, DEVELOPER, PROFILES, SAFE, profile_targets
from disksweep.scheduler import (
    TASK_NAME,
    build_create_command,
    build_delete_command,
    disksweep_command,
)
from disksweep.targets.registry import get_targets, target_ids
from disksweep.units import human_size, parse_size

# --------------------------------------------------------------------------- units


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("10MB", 10 * 1024**2),
        ("1.5 GB", int(1.5 * 1024**3)),
        ("4096", 4096),
        ("2k", 2048),
        ("1TB", 1024**4),
        ("0", 0),
        (" 7 kib ", 7 * 1024),
    ],
)
def test_parse_size(text: str, expected: int) -> None:
    assert parse_size(text) == expected


@pytest.mark.parametrize("bad", ["", "abc", "10XB", "-5MB", "1.2.3GB"])
def test_parse_size_invalid(bad: str) -> None:
    with pytest.raises(ValueError):
        parse_size(bad)


def test_human_size() -> None:
    assert human_size(0) == "0 B"
    assert human_size(1023) == "1023 B"
    assert human_size(1536) == "1.5 KB"
    assert human_size(450 * 1024**3) == "450.0 GB"
    assert human_size(-2048) == "-2.0 KB"


# --------------------------------------------------------------------------- profiles


def test_profiles_reference_real_targets() -> None:
    for ids in PROFILES.values():
        assert set(ids) <= set(target_ids())


def test_safe_profile_only_safe_targets() -> None:
    assert all(t.risk is Risk.SAFE for t in get_targets(SAFE))


def test_profiles_nest() -> None:
    assert set(SAFE) < set(DEVELOPER) < set(AGGRESSIVE)


def test_hibernation_never_in_a_profile() -> None:
    assert all("hibernation_off" not in ids for ids in PROFILES.values())


def test_profile_lookup() -> None:
    assert profile_targets("Safe") == SAFE
    with pytest.raises(KeyError):
        profile_targets("yolo")


# --------------------------------------------------------------------------- scheduler


def test_weekly_command() -> None:
    cmd = build_create_command("safe", "weekly", at="04:30", day="sat", executable="C:/ds.exe")
    assert cmd[:3] == ["schtasks", "/Create", "/F"]
    assert cmd[cmd.index("/TN") + 1] == TASK_NAME
    assert cmd[cmd.index("/SC") + 1] == "WEEKLY"
    assert cmd[cmd.index("/D") + 1] == "SAT"
    assert cmd[cmd.index("/ST") + 1] == "04:30"
    assert cmd[cmd.index("/RL") + 1] == "LIMITED"
    assert "clean --profile safe --apply --yes --quiet" in cmd[cmd.index("/TR") + 1]


def test_daily_and_highest() -> None:
    cmd = build_create_command("developer", "daily", highest=True)
    assert "/D" not in cmd
    assert cmd[cmd.index("/RL") + 1] == "HIGHEST"


def test_module_invocation_when_not_frozen() -> None:
    assert disksweep_command("safe")[1:3] == ["-m", "disksweep"]


@pytest.mark.parametrize(
    "kwargs",
    [{"profile": "nope"}, {"frequency": "hourly"}, {"at": "25:00"}, {"day": "FUNDAY"}],
)
def test_schedule_validation(kwargs: dict[str, str]) -> None:
    with pytest.raises(ValueError):
        build_create_command(**kwargs)  # type: ignore[arg-type]


def test_delete_command() -> None:
    assert build_delete_command() == ["schtasks", "/Delete", "/F", "/TN", TASK_NAME]


# --------------------------------------------------------------------------- large files


def test_large_file_thresholds(tmp_path: Path) -> None:
    make_tree(tmp_path, {"big_old.bin": b"x" * 2000, "big_new.bin": b"y" * 3000, "s": b"z"})
    old = time.time() - 400 * 86400
    os.utime(tmp_path / "big_old.bin", (old, old))

    files = find_large_files([str(tmp_path)], min_size=1000)
    assert [Path(f.path).name for f in files] == ["big_new.bin", "big_old.bin"]

    aged = find_large_files([str(tmp_path)], min_size=1000, older_than_days=365)
    assert [Path(f.path).name for f in aged] == ["big_old.bin"]
    assert aged[0].age_days > 390

    assert len(find_large_files([str(tmp_path)], min_size=1, top=2)) == 2
    assert len(find_large_files([str(tmp_path)], min_size=1, top=None)) == 3


# --------------------------------------------------------------------------- update check


def test_version_compare() -> None:
    assert updatecheck.is_newer("v1.2.0", "1.1.9")
    assert not updatecheck.is_newer("1.0.0", "1.0.0")
    assert not updatecheck.is_newer("0.9", "1.0.0")


def test_update_check_network_failure_is_silent(monkeypatch: pytest.MonkeyPatch) -> None:
    def fail(*a: object, **k: object) -> None:
        raise OSError("offline")

    monkeypatch.setattr(updatecheck.urllib.request, "urlopen", fail)
    assert updatecheck.check_for_update() is None


def test_no_network_imports_outside_updatecheck() -> None:
    src = Path(__file__).resolve().parents[1] / "src" / "disksweep"
    for path in src.rglob("*.py"):
        if path.name == "updatecheck.py":
            continue
        text = path.read_text(encoding="utf-8")
        for mod in ("urllib", "http.client", "socket", "requests"):
            assert f"import {mod}" not in text, f"{path.name} imports {mod}"


# --------------------------------------------------------------------------- logging


def _record() -> logging.LogRecord:
    rec = logging.LogRecord("disksweep.x", logging.INFO, __file__, 1, "deleted", None, None)
    rec.path = "C:/tmp/a"
    rec.bytes = 12
    return rec


def test_structured_formatters() -> None:
    line = KeyValueFormatter().format(_record())
    assert "deleted" in line and "path='C:/tmp/a'" in line and "bytes=12" in line
    data = json.loads(JsonFormatter().format(_record()))
    assert data["msg"] == "deleted" and data["bytes"] == 12 and data["level"] == "INFO"
