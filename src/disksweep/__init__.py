"""DiskSweep: safe, transparent disk cleanup for Windows.

The package is organised as a core library (``safety``, ``fsops``, ``targets``,
``engine``, ``analyzer``, ``duplicates``, ``largefiles``, ``scheduler``) shared
by a command-line interface (:mod:`disksweep.cli`) and a CustomTkinter GUI
(:mod:`disksweep.gui`).
"""

from disksweep.version import __version__

__all__ = ["__version__"]
