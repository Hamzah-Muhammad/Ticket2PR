# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller onefile spec for the Ticket2PR desktop app.

Console app on purpose: ticket2pr_gui.py hides the console once the window
is up, and child processes (git, gh, the Claude Code CLI the Agent SDK
spawns) inherit that hidden console instead of flashing their own.

The SDK's 253 MB bundled claude.exe is deliberately NOT packaged: the app
needs a Claude Code login anyway, so it uses the Claude Code already on PATH
and shows an Install link if there is none.

Run from the repo root: `venv\\Scripts\\python -m PyInstaller Ticket2PR.spec --noconfirm`
Output: dist/Ticket2PR.exe
"""

from pathlib import Path

ROOT = Path(SPECPATH)  # noqa: F821 -- injected by PyInstaller

VERSION = "0.2.0"
FILEVERS = (0, 2, 0, 0)

VERSION_INFO_PATH = ROOT / "version_info.txt"
VERSION_INFO_PATH.write_text(
    "VSVersionInfo(\n"
    "  ffi=FixedFileInfo(\n"
    f"    filevers={FILEVERS!r},\n"
    f"    prodvers={FILEVERS!r},\n"
    "    mask=0x3f,\n"
    "    flags=0x0,\n"
    "    OS=0x40004,\n"
    "    fileType=0x1,\n"
    "    subtype=0x0,\n"
    "    date=(0, 0),\n"
    "  ),\n"
    "  kids=[\n"
    "    StringFileInfo(\n"
    "      [\n"
    "        StringTable(\n"
    "          '040904B0',\n"
    "          [\n"
    "            StringStruct('CompanyName', 'Hamzah Muhammad (@Humzeeny)'),\n"
    "            StringStruct('FileDescription', 'Ticket2PR - GitHub issues in, pull requests out'),\n"
    f"            StringStruct('FileVersion', {VERSION!r}),\n"
    "            StringStruct('InternalName', 'Ticket2PR'),\n"
    "            StringStruct('OriginalFilename', 'Ticket2PR.exe'),\n"
    "            StringStruct('ProductName', 'Ticket2PR'),\n"
    f"            StringStruct('ProductVersion', {VERSION!r}),\n"
    "          ],\n"
    "        )\n"
    "      ]\n"
    "    ),\n"
    "    VarFileInfo([VarStruct('Translation', [1033, 1200])]),\n"
    "  ],\n"
    ")\n",
    encoding="utf-8",
)

a = Analysis(  # noqa: F821
    ["ticket2pr_gui.py"],
    pathex=[str(ROOT)],
    binaries=[],
    datas=[(str(ROOT / "Ticket2PR.ico"), ".")],
    hiddenimports=["anyio._backends._asyncio"],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)

# Never ship the SDK's bundled CLI (see module docstring).
a.datas = [d for d in a.datas if "_bundled" not in d[0].replace("\\", "/")]
a.binaries = [b for b in a.binaries if "_bundled" not in b[0].replace("\\", "/")]

pyz = PYZ(a.pure)  # noqa: F821

exe = EXE(  # noqa: F821
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="Ticket2PR",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,
    icon=str(ROOT / "Ticket2PR.ico"),
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    version=str(VERSION_INFO_PATH),
)
