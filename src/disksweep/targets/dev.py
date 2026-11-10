"""Developer tool caches: package managers, build tools, Docker and WSL."""

from __future__ import annotations

import os
import re

from disksweep import osutils
from disksweep.fsops import iter_files
from disksweep.models import Risk
from disksweep.targets.base import (
    CleanupTarget,
    CommandTarget,
    DirectoryTarget,
    TargetContext,
    expand_specs,
)
from disksweep.units import human_size


class DevCache(DirectoryTarget):
    category = "developer"
    risk = Risk.SAFE


class NpmCache(DevCache):
    id = "npm_cache"
    name = "npm cache"
    description = "npm's package download cache (_cacache). Packages are re-downloaded on demand."
    specs = (("LOCALAPPDATA", "npm-cache", "_cacache"), ("APPDATA", "npm-cache", "_cacache"))


class YarnCache(DevCache):
    id = "yarn_cache"
    name = "Yarn cache"
    description = "Yarn (classic and Berry) global package cache."
    specs = (("LOCALAPPDATA", "Yarn", "Cache"), ("LOCALAPPDATA", "Yarn", "Berry", "cache"))


class PipCache(DevCache):
    id = "pip_cache"
    name = "pip cache"
    description = "pip's HTTP and wheel cache."
    specs = (("LOCALAPPDATA", "pip", "Cache"), ("PIP_CACHE_DIR",))


class NuGetCache(DevCache):
    id = "nuget_cache"
    name = "NuGet HTTP cache"
    description = "NuGet HTTP/plugin caches and scratch files."
    specs = (
        ("LOCALAPPDATA", "NuGet", "v3-cache"),
        ("LOCALAPPDATA", "NuGet", "plugins-cache"),
        ("TEMP", "NuGetScratch"),
    )


class NuGetPackages(DevCache):
    id = "nuget_packages"
    name = "NuGet global packages"
    description = "~/.nuget/packages. Restored on next build (requires network)."
    risk = Risk.MODERATE
    process_names = ("devenv.exe", "msbuild.exe")
    specs = (("USERPROFILE", ".nuget", "packages"), ("NUGET_PACKAGES",))


class GradleCache(DevCache):
    id = "gradle_cache"
    name = "Gradle caches"
    description = "~/.gradle/caches. Dependencies are re-downloaded on next build."
    risk = Risk.MODERATE
    specs = (("USERPROFILE", ".gradle", "caches"), ("GRADLE_USER_HOME", "caches"))


class MavenRepo(DevCache):
    id = "maven_repo"
    name = "Maven local repository"
    description = "~/.m2/repository. Dependencies are re-downloaded on next build."
    risk = Risk.MODERATE
    specs = (("USERPROFILE", ".m2", "repository"),)


class CargoCache(DevCache):
    id = "cargo_cache"
    name = "Cargo registry cache"
    description = "Downloaded crates and git checkouts in ~/.cargo. Re-fetched on next build."
    risk = Risk.MODERATE
    process_names = ("cargo.exe",)
    specs = (
        ("USERPROFILE", ".cargo", "registry", "cache"),
        ("USERPROFILE", ".cargo", "registry", "src"),
        ("USERPROFILE", ".cargo", "git", "checkouts"),
        ("CARGO_HOME", "registry", "cache"),
        ("CARGO_HOME", "registry", "src"),
        ("CARGO_HOME", "git", "checkouts"),
    )


def _dir_size(paths: list[str]) -> int:
    """Total size of regular files under ``paths``; never follows symlinks/junctions."""
    return sum(st.st_size for p in paths for _, st in iter_files(p))


class PnpmStore(CommandTarget):
    id = "pnpm_store"
    name = "pnpm store (prune)"
    description = "Runs `pnpm store prune` to remove packages no project references."
    risk = Risk.SAFE
    category = "developer"
    executable = "pnpm"

    def build_command(self, ctx: TargetContext) -> list[str] | None:
        exe = osutils.which("pnpm")
        return [exe, "store", "prune"] if exe else None

    def _store(self, ctx: TargetContext) -> list[str]:
        return expand_specs((("LOCALAPPDATA", "pnpm", "store"),), ctx.environ)

    def measure(self, ctx: TargetContext) -> tuple[int, bool, str]:
        size = _dir_size(self._store(ctx))
        return size, True, "store size; prune frees only unreferenced packages"

    def current_size(self, ctx: TargetContext) -> int | None:
        return _dir_size(self._store(ctx))


_DOCKER_SIZE = re.compile(r"([\d.]+)\s*([kKMGT]?B)")
_DECIMAL = {"B": 1, "KB": 1000, "MB": 1000**2, "GB": 1000**3, "TB": 1000**4}


def parse_docker_size(text: str) -> int:
    """Parse Docker's decimal sizes such as ``"1.2GB (40%)"`` or ``"512kB"``."""
    m = _DOCKER_SIZE.search(text or "")
    if not m:
        return 0
    return int(float(m.group(1)) * _DECIMAL[m.group(2).upper()])


class DockerPrune(CommandTarget):
    id = "docker_prune"
    name = "Docker unused data"
    description = (
        "Runs `docker system prune -f`: removes stopped containers, unused networks, "
        "dangling images and build cache. Volumes are kept."
    )
    risk = Risk.MODERATE
    category = "developer"
    executable = "docker"

    def build_command(self, ctx: TargetContext) -> list[str] | None:
        exe = osutils.which("docker")
        return [exe, "system", "prune", "-f"] if exe else None

    def measure(self, ctx: TargetContext) -> tuple[int, bool, str]:
        exe = osutils.which("docker")
        if not exe:
            return 0, True, "docker not found"
        res = osutils.run_cmd([exe, "system", "df", "--format", "{{.Reclaimable}}"], timeout=60)
        if not res.ok:
            return 0, True, "Docker engine not running"
        total = sum(parse_docker_size(line) for line in res.stdout.splitlines())
        return total, True, "reclaimable according to `docker system df`"

    def current_size(self, ctx: TargetContext) -> int | None:
        return self.measure(ctx)[0]


class WslCompact(CommandTarget):
    id = "wsl_compact"
    name = "Compact WSL / Docker virtual disks"
    description = (
        "Shuts down WSL and compacts ext4.vhdx files with diskpart so space freed "
        "inside Linux is returned to Windows."
    )
    risk = Risk.ADVANCED
    requires_admin = True
    category = "developer"
    executable = "wsl"
    process_names = ("Docker Desktop.exe",)
    timeout = 60 * 60

    VHDX_SPECS = (
        ("LOCALAPPDATA", "Packages", "*", "LocalState", "ext4.vhdx"),
        ("LOCALAPPDATA", "wsl", "*", "ext4.vhdx"),
        ("LOCALAPPDATA", "Docker", "wsl", "*", "*.vhdx"),
        ("LOCALAPPDATA", "Docker", "wsl", "*", "*", "*.vhdx"),
    )

    def vhdx_files(self, ctx: TargetContext) -> list[str]:
        return [p for p in expand_specs(self.VHDX_SPECS, ctx.environ) if os.path.isfile(p)]

    def build_command(self, ctx: TargetContext) -> list[str] | None:
        if not osutils.which("wsl") or not self.vhdx_files(ctx):
            return None
        return ["wsl", "--shutdown"]

    def describe(self, ctx: TargetContext) -> str:
        files = self.vhdx_files(ctx)
        return "wsl --shutdown; diskpart compact vdisk on " + ", ".join(files)

    def current_size(self, ctx: TargetContext) -> int | None:
        return sum(os.path.getsize(p) for p in self.vhdx_files(ctx))

    def measure(self, ctx: TargetContext) -> tuple[int, bool, str]:
        files = self.vhdx_files(ctx)
        size = sum(os.path.getsize(p) for p in files)
        return 0, True, f"{len(files)} virtual disk(s), {human_size(size)} total; gain unknown"

    def run_steps(self, ctx: TargetContext) -> tuple[bool, str]:
        shutdown = osutils.run_cmd(["wsl", "--shutdown"], timeout=300)
        if not shutdown.ok:
            return False, "wsl --shutdown failed"
        messages: list[str] = []
        ok = True
        for vhdx in self.vhdx_files(ctx):
            script = (
                f'select vdisk file="{vhdx}"\nattach vdisk readonly\ncompact vdisk\ndetach vdisk\n'
            )
            res = osutils.run_cmd(["diskpart"], timeout=self.timeout, input_text=script)
            if ctx.audit:
                ctx.audit.record_event("compact_vdisk", path=vhdx, returncode=res.returncode)
            ok = ok and res.ok
            messages.append(f"{os.path.basename(vhdx)}: {'ok' if res.ok else 'failed'}")
        return ok, "; ".join(messages)


TARGETS: tuple[type[CleanupTarget], ...] = (
    NpmCache,
    YarnCache,
    PnpmStore,
    PipCache,
    NuGetCache,
    NuGetPackages,
    GradleCache,
    MavenRepo,
    CargoCache,
    DockerPrune,
    WslCompact,
)
