# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for god2iso.exe (Windows one-file console build).

Build (on Windows, from this folder):
    python -m pip install pyinstaller
    python god2iso.py audit
    python -m PyInstaller --noconfirm --clean god2iso.spec
    -> dist\\god2iso.exe

Notes on safety-by-construction:
  * `excludes` removes every network-capable Python module from the bundle
    (socket, ssl, http, urllib, email, ftplib, ...) plus unused heavy stdlib
    (tkinter, unittest, sqlite3, xml, ...).  The resulting .exe structurally
    cannot open a network connection - the bundled interpreter does not even
    contain those modules.
  * `audit_result.txt` embeds the output of `python god2iso.py audit` run on
    the exact source being packaged; `god2iso.exe audit` verifies it.
  * UPX is NOT used (avoids antivirus false positives).
"""

import os

block_cipher = None

a = Analysis(
    ['god2iso.py'],
    pathex=[],
    binaries=[],
    datas=[('audit_result.txt', '.'),
           ('assets/god2iso.ico', 'assets'),
           ('assets/god2iso.png', 'assets')],
    hiddenimports=['gui'],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        # network-capable modules - must never ship in the .exe
        # (urllib.parse/urllib.error stay: pure string parsing, required by
        #  pathlib; the network client urllib.request is excluded)
        'socket', 'ssl', 'http', 'http.client', 'urllib.request',
        'email', 'ftplib', 'smtplib', 'telnetlib', 'xmlrpc', 'aiohttp',
        'asyncio', 'webbrowser',
        # unused heavy stdlib - smaller, cleaner bundle
        'unittest', 'pydoc', 'doctest', 'sqlite3', 'xml',
        'curses', 'readline', 'test',
    ],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='god2iso',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,                 # CLI tool: keep the console window
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon='assets/god2iso.ico',
    version='version_info.txt',
)
