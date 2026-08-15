# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for the ONEDIR variant of god2iso (Windows).

Why a onedir variant exists: antivirus heuristics flag self-extracting
single-file executables more often than folder-based ones.  The onedir
build (god2iso\\god2iso.exe + god2iso\\_internal\\...) is the alternative
distribution for users who still get false positives with the onefile exe.
Identical source, identical excludes (no network-capable modules), same
version resource and audit proof.
"""

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
        'socket', 'ssl', 'http', 'http.client', 'urllib.request',
        'email', 'ftplib', 'smtplib', 'telnetlib', 'xmlrpc', 'aiohttp',
        'asyncio', 'webbrowser',
        'unittest', 'pydoc', 'doctest', 'sqlite3', 'xml',
        'curses', 'readline', 'test',
    ],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='god2iso',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon='assets/god2iso.ico',
    version='version_info.txt',
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name='god2iso',
)
