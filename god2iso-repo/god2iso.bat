@echo off
rem god2iso.py launcher for Windows (double-click or run from cmd/PowerShell)
rem Usage:  god2iso.bat convert <path-to-.live-or-folder>
setlocal
set "SCRIPT=%~dp0god2iso.py"

where python >nul 2>nul
if %errorlevel%==0 (
    python "%SCRIPT%" %*
    exit /b
)

where py >nul 2>nul
if %errorlevel%==0 (
    py -3 "%SCRIPT%" %*
    exit /b
)

echo [god2iso] Python 3 was not found. Install it from https://www.python.org/downloads/
echo [god2iso] (tick "Add Python to PATH" during installation)
exit /b 1
