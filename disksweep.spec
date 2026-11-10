# PyInstaller spec: builds dist/DiskSweep.exe (GUI) and dist/disksweep-cli.exe (console).
#   pyinstaller --clean --noconfirm disksweep.spec
# -*- mode: python ; coding: utf-8 -*-

from PyInstaller.utils.hooks import collect_data_files

ctk_datas = collect_data_files("customtkinter")
common = dict(pathex=["src"], hookspath=[], runtime_hooks=[], excludes=["pytest"])

gui_a = Analysis(["packaging/gui_entry.py"], datas=ctk_datas, **common)
gui_pyz = PYZ(gui_a.pure)
gui_exe = EXE(
    gui_pyz,
    gui_a.scripts,
    gui_a.binaries,
    gui_a.datas,
    name="DiskSweep",
    console=False,
    upx=False,
    # Runs as the normal user; elevation is requested only for admin-only targets.
    uac_admin=False,
)

cli_a = Analysis(["packaging/cli_entry.py"], datas=ctk_datas, **common)
cli_pyz = PYZ(cli_a.pure)
cli_exe = EXE(
    cli_pyz,
    cli_a.scripts,
    cli_a.binaries,
    cli_a.datas,
    name="disksweep-cli",
    console=True,
    upx=False,
    uac_admin=False,
)
