# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project uses
[Semantic Versioning](https://semver.org/).

## [Unreleased]

## [1.1.0] - 2026-11-10

### Changed
- Redesigned GUI: sidebar navigation, summary cards (drive-usage ring, reclaimable,
  selected), targets grouped by category with tinted risk badges and relative-size bars,
  in-app confirmation and result dialogs, and the audit log shown as a table.
  Shared design tokens in `gui/theme.py`; text and button colors meet WCAG 4.5:1 in
  light and dark themes.
- DISM cleanup, hibernation and shadow-storage targets now have their own *advanced* category.

### Added
- Analyzer charts: a *Largest subfolders* bar chart (click a bar to drill down, **Up** to go
  back) and a *Space by file type* chart, with hover tooltips and a Charts/Tables toggle.
- `disksweep analyze` prints a *Space by file type* table; JSON/CSV exports include file types.
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
