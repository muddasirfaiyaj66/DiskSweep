# Contributing to DiskSweep

Thanks for helping! DiskSweep deletes files on other people's computers, so the bar for
**safety** is higher than for most projects. Please read this first.

## Development setup

```powershell
python -m venv .venv
.venv\Scripts\activate          # Linux/macOS: source .venv/bin/activate
pip install -e ".[dev]"
pytest
ruff check . && ruff format --check .
```

You can develop and test on Linux or macOS. Windows-only calls go through
`disksweep/osutils.py`, and the tests mock them.

## Ground rules

1. **Only `fsops.py` deletes.** Never call `os.remove`, `os.unlink`, `os.rmdir`,
   `shutil.rmtree` or `send2trash` anywhere else. `test_only_fsops_deletes_files` enforces this.
2. **Never weaken `safety.py`.** Protected paths can be added but not removed. A PR that
   loosens the guard needs a strong justification and a maintainer security review.
3. **Dry run stays the default.** New commands or options must not delete without an explicit
   `--apply` (CLI) or a confirmation (GUI).
4. **Windows calls go through `osutils.py`** so they can be mocked.
5. **No network access.** `updatecheck.py` is the only exception, and it's opt-in.
6. **The GUI must not block.** Long work runs in `gui/workers.TaskRunner`.

## Adding a cleanup target

Most targets are a few lines. Subclass `DirectoryTarget` (folders) or `CommandTarget`
(external tool) in `src/disksweep/targets/`:

```python
class MyToolCache(DirectoryTarget):
    id = "mytool_cache"
    name = "MyTool cache"
    description = "Downloaded MyTool artifacts. Re-downloaded on demand."
    risk = Risk.SAFE  # safe | moderate | advanced
    category = "developer"
    process_names = ("mytool.exe",)  # skipped while running
    specs = (("LOCALAPPDATA", "MyTool", "Cache"),)
    min_age_hours = 0  # >0 to keep recently used files
```

Then:

- add it to the module's `TARGETS` tuple, and to a profile in `profiles.py` if it belongs there;
- add a test in `tests/test_targets.py` that builds a fake layout in `tmp_path` and checks
  what is deleted **and what is kept**;
- in the PR, link documentation showing the data is safe to delete.

## Tests

- Use `tmp_path` for everything. Never touch real user folders.
- The autouse fixture in `tests/conftest.py` stubs services, process checks and external
  commands. An unexpected external command fails the test.
- Safety-relevant changes need a test that proves the dangerous case is blocked.

## Screenshots

Regenerate all GUI screenshots (Windows only) with:

```powershell
python tools/capture_screenshots.py
```

The script drives the real GUI through scan, confirmation, cleanup, analyzer and audit log
against a throwaway demo environment, so no personal paths appear. The demo uses sparse
files, and the script aborts if any of them would take real disk space. Windows are
captured with `PrintWindow`, so the rest of the desktop is never recorded.

## Commit and PR checklist

- [ ] `pytest` passes
- [ ] `ruff check .` and `ruff format --check .` pass
- [ ] Every new public function has type hints and a docstring
- [ ] `CHANGELOG.md` updated under *Unreleased*
