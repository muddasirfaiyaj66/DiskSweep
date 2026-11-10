# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project uses
[Semantic Versioning](https://semver.org/).

## [Unreleased]

### Added
- `tools/capture_screenshots.py` and a 12-step GUI screenshot walkthrough in the README.

## [1.0.0] - 2026-11-10

### Added
- Core library shared by the CLI (`disksweep`) and the CustomTkinter GUI (`disksweep-gui`).
- 27 cleanup targets with id, description, risk level and admin flag:
  - System: user/Windows temp, Windows Update cache (stops and restarts `wuauserv`),
    `Windows.old`, crash dumps (user and system), Windows logs, thumbnail cache, Recycle Bin.
  - Browsers: Chrome, Edge, Firefox, Brave caches (skipped while the browser is running).
  - Developer: npm, Yarn, pnpm (`store prune`), pip, NuGet, Gradle, Maven, Cargo,
    `docker system prune`, WSL/Docker vhdx compaction.
  - Advanced: DISM `/StartComponentCleanup`, hibernation off, shadow-storage resize.
- Profiles: `safe`, `developer`, `aggressive`.
- Parallel disk analyzer with top-N folders/files and JSON/CSV export.
- Duplicate finder (size, then partial BLAKE2b, then full BLAKE2b) with Recycle Bin removal.
- Large/old file finder.
- Scheduled cleanup through Windows Task Scheduler.
- Safety: dry run by default, hard-coded protected paths (including redirected known
  folders), no symlink/junction following, containment checks, locked-file tolerance,
  JSONL audit log, before/after free space.
- Opt-in update check (off by default). No telemetry.
- GitHub Actions: ruff, pytest on Windows and Linux, PyInstaller release build on tags.
