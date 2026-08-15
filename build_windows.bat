@echo off
rem ===========================================================================
rem  god2iso.exe - Windows build script (transparency & reproducibility)
rem
rem  Rebuilds the standalone god2iso.exe from the exact source in this
rem  folder, so anyone can verify the published .exe was built from this
rem  code.  The build:
rem    1. runs the offline self-audit on the source (must pass),
rem    2. embeds the audit result into the executable,
rem    3. builds a one-file console .exe with PyInstaller (no UPX),
rem    4. prints the SHA-256 of the result.
rem
rem  Requirements: Python 3.9+ installed and on PATH (https://www.python.org)
rem ===========================================================================
setlocal
cd /d "%~dp0"

where python >nul 2>nul
if errorlevel 1 (
    echo [god2iso] Python 3 was not found. Install it from https://www.python.org/downloads/
    echo [god2iso] ^(tick "Add Python to PATH" during installation^)
    exit /b 1
)

echo [1/4] installing PyInstaller...
python -m pip install --quiet pyinstaller
if errorlevel 1 (
    echo [god2iso] failed to install PyInstaller
    exit /b 1
)

echo [2/4] running offline source audit...
python god2iso.py audit
if errorlevel 1 (
    echo [god2iso] audit FAILED - refusing to build
    exit /b 1
)
> audit_result.txt echo god2iso.py source audit: OK - no network-capable imports, all modules compile (generated %date% %time%)

echo [3/4] building god2iso.exe...
python -m PyInstaller --noconfirm --clean god2iso.spec
if errorlevel 1 (
    echo [god2iso] PyInstaller build failed
    exit /b 1
)

echo [4/4] checksum...
certutil -hashfile "dist\god2iso.exe" SHA256 | findstr /v "hash" > "dist\god2iso.exe.sha256"
for /f "delims=" %%i in (dist\god2iso.exe.sha256) do set "HASH=%%i"
echo.
echo [god2iso] built dist\god2iso.exe
echo [god2iso] SHA-256: %HASH%
echo [god2iso] verify offline:  dist\god2iso.exe audit
echo [god2iso] convert:        dist\god2iso.exe convert ^<path-to-.live-or-folder^>
echo [god2iso] or double-click dist\god2iso.exe for the wizard
endlocal
