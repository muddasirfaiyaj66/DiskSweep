"""PyInstaller entry point for the console executable (disksweep-cli.exe)."""

import sys

from disksweep.cli import main

if __name__ == "__main__":
    sys.exit(main())
