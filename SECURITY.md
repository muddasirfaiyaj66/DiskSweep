# Security policy

DiskSweep deletes files, so we treat **any way to make it delete something it shouldn't**
as a security vulnerability. That includes:

- a path inside a protected folder (Windows, Program Files, Documents, Desktop, Pictures, ...)
  getting deleted or recycled;
- symlinks, junctions, mount points, `..` segments, short (8.3) names, alternate data streams,
  or case tricks that get around the path guard;
- a dry run (no `--apply`) that modifies anything;
- personal files deleted permanently when they should have gone to the Recycle Bin;
- privilege escalation through the elevation or scheduled-task features;
- any unexpected network traffic.

## Reporting

Please **don't open a public issue**. Report privately through GitHub's
[security advisory form](https://github.com/muddasirfaiyaj66/DiskSweep/security/advisories/new).
Include:

- the DiskSweep version (`disksweep --version`) and your Windows version;
- the exact command or GUI steps;
- the relevant lines from the audit log (`disksweep log`), with personal paths removed if
  you prefer.

We aim to acknowledge reports within 72 hours and to ship a fix for confirmed issues as
soon as possible. Reporters are credited in the changelog unless they ask not to be.

## Supported versions

Only the latest release gets security fixes.

## Design notes for reviewers

- `src/disksweep/safety.py`: `PathGuard`, the protected-path rules.
- `src/disksweep/fsops.py`: the only code that deletes. It checks every path with the guard,
  never follows links, and tolerates locked files.
- `tests/test_safety.py`, `tests/test_fsops.py`: tests showing that protected paths and
  link escapes are blocked.
