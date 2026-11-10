"""Cleanup targets operate on fake Windows layouts inside tmp."""

from __future__ import annotations

from pathlib import Path

import pytest

from conftest import make_old, make_tree
from disksweep import osutils
from disksweep.models import Risk
from disksweep.targets import base as target_base
from disksweep.targets.advanced import parse_shadow_used
from disksweep.targets.base import DirectoryTarget, TargetContext, expand_specs
from disksweep.targets.browsers import ChromeCache, FirefoxCache
from disksweep.targets.dev import DockerPrune, NpmCache, parse_docker_size
from disksweep.targets.registry import all_targets, get_targets, target_ids
from disksweep.targets.system import (
    RecycleBin,
    ThumbnailCache,
    UserTemp,
    WindowsOld,
    WindowsUpdateCache,
)


def env_path(ctx: TargetContext, var: str) -> Path:
    return Path(ctx.environ[var])


def test_all_targets_have_metadata() -> None:
    ids = target_ids()
    assert len(ids) == len(set(ids)), "duplicate target ids"
    for t in all_targets():
        assert t.id and t.name and t.description
        assert isinstance(t.risk, Risk)
        assert isinstance(t.requires_admin, bool)
        assert callable(t.scan) and callable(t.clean)


def test_required_targets_exist() -> None:
    required = {
        "user_temp", "windows_temp", "windows_update", "windows_old", "crash_dumps",
        "windows_logs", "thumbnail_cache", "recycle_bin", "chrome_cache", "edge_cache",
        "firefox_cache", "brave_cache", "npm_cache", "yarn_cache", "pnpm_store", "pip_cache",
        "nuget_cache", "gradle_cache", "maven_repo", "cargo_cache", "docker_prune",
        "wsl_compact", "dism_cleanup", "hibernation_off", "shadow_storage",
    }  # fmt: skip
    assert required <= set(target_ids())


def test_get_targets_unknown_id() -> None:
    with pytest.raises(KeyError):
        get_targets(["nope"])


def test_expand_specs_globs_and_dedup(tmp_path: Path) -> None:
    for prof in ("Default", "Profile 1"):
        (tmp_path / "UD" / prof / "Cache").mkdir(parents=True)
    env = {"X": str(tmp_path), "Y": str(tmp_path)}
    found = expand_specs([("X", "UD", "*", "Cache"), ("Y", "UD", "*", "Cache"), ("MISSING",)], env)
    assert len(found) == 2


def test_user_temp_respects_age(ctx: TargetContext) -> None:
    temp = env_path(ctx, "TEMP")
    make_tree(temp, {"old.tmp": b"o" * 100, "fresh.tmp": b"f" * 10, "d/old2.tmp": b"x" * 5})
    make_old(temp / "old.tmp", 2)
    make_old(temp / "d" / "old2.tmp", 2)
    t = UserTemp()
    scan = t.scan(ctx)
    assert scan.bytes == 105
    assert (temp / "old.tmp").exists()  # scan never deletes
    res = t.clean(ctx, dry_run=False)
    assert res.bytes_freed == 105
    assert not (temp / "old.tmp").exists()
    assert (temp / "fresh.tmp").exists()


def test_clean_defaults_to_dry_run(ctx: TargetContext) -> None:
    temp = env_path(ctx, "TEMP")
    make_tree(temp, {"old.tmp": b"o"})
    make_old(temp / "old.tmp", 2)
    res = UserTemp().clean(ctx)
    assert res.dry_run
    assert (temp / "old.tmp").exists()


def test_missing_target_unavailable(ctx: TargetContext) -> None:
    res = ChromeCache().scan(ctx)
    assert not res.available


def test_chrome_cache_all_profiles(ctx: TargetContext) -> None:
    ud = env_path(ctx, "LOCALAPPDATA") / "Google" / "Chrome" / "User Data"
    make_tree(ud, {
        "Default/Cache/Cache_Data/f_1": b"a" * 10,
        "Profile 1/Code Cache/js/x": b"b" * 20,
        "Default/History": b"never touched",
        "Default/Login Data": b"never touched",
    })  # fmt: skip
    t = ChromeCache()
    assert t.scan(ctx).bytes == 30
    t.clean(ctx, dry_run=False)
    assert (ud / "Default" / "History").exists()
    assert (ud / "Default" / "Login Data").exists()
    assert not (ud / "Default" / "Cache" / "Cache_Data" / "f_1").exists()


def test_browser_skipped_while_running(ctx: TargetContext, monkeypatch: pytest.MonkeyPatch) -> None:
    prof = env_path(ctx, "LOCALAPPDATA") / "Mozilla" / "Firefox" / "Profiles" / "abc.default"
    make_tree(prof, {"cache2/entries/1": b"z" * 10})
    monkeypatch.setattr(osutils, "running_processes", lambda names: {"firefox.exe"})
    res = FirefoxCache().clean(ctx, dry_run=False)
    assert not res.ran
    assert "firefox.exe" in res.note
    assert (prof / "cache2" / "entries" / "1").exists()


def test_admin_target_skipped_without_admin(ctx: TargetContext) -> None:
    ctx.is_admin = False
    dl = env_path(ctx, "SystemRoot") / "SoftwareDistribution" / "Download"
    make_tree(dl, {"pkg.cab": b"c"})
    res = WindowsUpdateCache().clean(ctx, dry_run=False)
    assert not res.ran
    assert "administrator" in res.note
    assert (dl / "pkg.cab").exists()


def test_windows_update_stops_and_restarts_service(
    ctx: TargetContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[str] = []
    monkeypatch.setattr(osutils, "service_running", lambda name: True)
    monkeypatch.setattr(osutils, "stop_service", lambda name: calls.append(f"stop {name}") or True)
    monkeypatch.setattr(
        osutils, "start_service", lambda name: calls.append(f"start {name}") or True
    )
    dl = env_path(ctx, "SystemRoot") / "SoftwareDistribution" / "Download"
    make_tree(dl, {"a/pkg.cab": b"c" * 9})
    res = WindowsUpdateCache().clean(ctx, dry_run=False)
    assert calls == ["stop wuauserv", "start wuauserv"]
    assert res.bytes_freed == 9
    assert dl.is_dir() and not any(dl.iterdir())


def test_service_restarted_even_if_cleaning_fails(
    ctx: TargetContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[str] = []
    monkeypatch.setattr(osutils, "service_running", lambda name: True)
    monkeypatch.setattr(osutils, "start_service", lambda name: calls.append("start") or True)
    make_tree(env_path(ctx, "SystemRoot") / "SoftwareDistribution" / "Download", {"x": b"1"})

    def boom(self: DirectoryTarget, ctx: TargetContext, dry_run: bool) -> None:
        raise RuntimeError("boom")

    monkeypatch.setattr(target_base.DirectoryTarget, "_run", boom)
    with pytest.raises(RuntimeError):
        WindowsUpdateCache().clean(ctx, dry_run=False)
    assert calls == ["start"]


def test_windows_old_removed_entirely(ctx: TargetContext) -> None:
    old = env_path(ctx, "SystemDrive") / "Windows.old"
    make_tree(old, {"Windows/System32/a.dll": b"1" * 4, "Program Files/x": b"2"})
    res = WindowsOld().clean(ctx, dry_run=False)
    assert res.bytes_freed == 5
    assert not old.exists()


def test_thumbnail_cache_only_matching(ctx: TargetContext) -> None:
    exp = env_path(ctx, "LOCALAPPDATA") / "Microsoft" / "Windows" / "Explorer"
    make_tree(exp, {"thumbcache_256.db": b"t" * 3, "ExplorerStartupLog.etl": b"k"})
    ThumbnailCache().clean(ctx, dry_run=False)
    assert not (exp / "thumbcache_256.db").exists()
    assert (exp / "ExplorerStartupLog.etl").exists()


def test_npm_cache(ctx: TargetContext) -> None:
    cache = env_path(ctx, "LOCALAPPDATA") / "npm-cache"
    make_tree(cache, {"_cacache/content-v2/sha512/aa": b"n" * 8, "_logs/debug.log": b"keep"})
    res = NpmCache().clean(ctx, dry_run=False)
    assert res.bytes_freed == 8
    assert (cache / "_logs" / "debug.log").exists()


def test_target_pointing_at_protected_folder_deletes_nothing(
    ctx: TargetContext, tmp_path: Path
) -> None:
    """Even a buggy/malicious target definition cannot delete protected data."""
    protected = tmp_path / "protected"
    make_tree(protected, {"thesis.docx": b"years of work"})
    ctx.environ["EVIL"] = str(protected)
    rogue = DirectoryTarget(specs=(("EVIL",),))
    rogue.id = "rogue"
    res = rogue.clean(ctx, dry_run=False)
    assert (protected / "thesis.docx").read_bytes() == b"years of work"
    assert res.files_deleted == 0
    assert res.skipped


def test_recycle_bin_uses_shell_api(ctx: TargetContext, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(osutils, "IS_WINDOWS", True)
    monkeypatch.setattr(osutils, "recycle_bin_size", lambda: (1234, 3))
    emptied: list[bool] = []
    monkeypatch.setattr(osutils, "empty_recycle_bin", lambda: emptied.append(True) or True)
    t = RecycleBin()
    assert t.scan(ctx).bytes == 1234
    dry = t.clean(ctx)
    assert dry.bytes_freed == 1234 and not emptied
    res = t.clean(ctx, dry_run=False)
    assert emptied == [True]
    assert res.bytes_freed == 1234


def test_docker_prune_command(ctx: TargetContext, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(osutils, "IS_WINDOWS", True)
    monkeypatch.setattr(osutils, "which", lambda name: f"C:/bin/{name}.exe")
    ran: list[list[str]] = []
    state = {"reclaimable": "2GB (50%)\n500MB (10%)\n"}

    def fake_run(args, timeout=0, input_text=None):  # type: ignore[no-untyped-def]
        ran.append(list(args))
        if "df" in args:
            return osutils.CommandResult(tuple(args), 0, state["reclaimable"], "")
        state["reclaimable"] = "0B\n0B\n"
        return osutils.CommandResult(tuple(args), 0, "Total reclaimed space: 2.5GB", "")

    monkeypatch.setattr(osutils, "run_cmd", fake_run)
    t = DockerPrune()
    assert t.scan(ctx).bytes == 2_500_000_000
    dry = t.clean(ctx)
    assert dry.dry_run and not any("prune" in a for a in ran)
    res = t.clean(ctx, dry_run=False)
    assert ["C:/bin/docker.exe", "system", "prune", "-f"] in ran
    assert res.bytes_freed == 2_500_000_000
    assert "reclaimed" in res.note


def test_command_targets_unavailable_off_windows(
    ctx: TargetContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(osutils, "IS_WINDOWS", False)
    for tid in ("dism_cleanup", "hibernation_off", "shadow_storage", "docker_prune"):
        (t,) = get_targets([tid])
        assert not t.scan(ctx).available
        assert not t.clean(ctx, dry_run=False).ran


def test_parsers() -> None:
    assert parse_docker_size("1.5GB (40%)") == 1_500_000_000
    assert parse_docker_size("512kB") == 512_000
    assert parse_docker_size("0B") == 0
    out = (
        "Shadow Copy Storage association\n"
        "   Used Shadow Copy Storage space: 2.5 GB (1%)\n"
        "   Used Shadow Copy Storage space: 512 MB (0%)\n"
    )
    assert parse_shadow_used(out) == int(2.5 * 1024**3) + 512 * 1024**2


def test_dry_run_mirrors_admin_skip(ctx: TargetContext) -> None:
    ctx.is_admin = False
    make_tree(env_path(ctx, "SystemRoot") / "SoftwareDistribution" / "Download", {"p": b"1"})
    res = WindowsUpdateCache().clean(ctx, dry_run=True)
    assert not res.ran and res.bytes_freed == 0


def test_dry_run_excludes_estimates(ctx: TargetContext, monkeypatch: pytest.MonkeyPatch) -> None:
    from disksweep.targets.dev import PnpmStore

    monkeypatch.setattr(osutils, "IS_WINDOWS", True)
    monkeypatch.setattr(osutils, "which", lambda name: "C:/bin/pnpm.cmd")
    make_tree(env_path(ctx, "LOCALAPPDATA") / "pnpm" / "store", {"v3/x": b"s" * 4096})
    t = PnpmStore()
    assert t.scan(ctx).estimated
    res = t.clean(ctx, dry_run=True)
    assert res.bytes_freed == 0
    assert "up to 4.0 KB" in res.note


def test_store_size_does_not_follow_junctions(tmp_path: Path) -> None:
    from conftest import try_symlink
    from disksweep.targets.dev import _dir_size

    make_tree(tmp_path / "store", {"a": b"a" * 100})
    make_tree(tmp_path / "elsewhere", {"big": b"b" * 10_000})
    try_symlink(tmp_path / "elsewhere", tmp_path / "store" / "link", directory=True)
    assert _dir_size([str(tmp_path / "store")]) == 100
