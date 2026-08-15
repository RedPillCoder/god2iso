# test_windows.ps1 - automated smoke test for god2iso.exe on Windows
#
# Usage:
#   powershell -ExecutionPolicy Bypass -File test_windows.ps1
#       (tests version/audit/help only)
#   powershell -ExecutionPolicy Bypass -File test_windows.ps1 -GodPath "C:\path\to\GOD"
#       (also converts, lists, extracts and rebuilds a real GOD package)
#
# Optional: -ExePath .\god2iso.exe   -OutDir C:\temp\god2iso_test
#
# Exit code 0 = all checks passed.

param(
    [string]$GodPath = "",
    [string]$ExePath = ".\god2iso.exe",
    [string]$OutDir = ""
)

$ErrorActionPreference = "Stop"
$script:pass = 0
$script:fail = 0

function Check([string]$name, [bool]$cond) {
    if ($cond) {
        $script:pass++
        Write-Host ("  [PASS] " + $name) -ForegroundColor Green
    } else {
        $script:fail++
        Write-Host ("  [FAIL] " + $name) -ForegroundColor Red
    }
}

Write-Host "==============================================" -ForegroundColor Cyan
Write-Host " god2iso.exe - Windows test" -ForegroundColor Cyan
Write-Host "==============================================" -ForegroundColor Cyan

if (-not (Test-Path $ExePath)) {
    Write-Host "ERROR: $ExePath not found. Put this script next to god2iso.exe." -ForegroundColor Red
    exit 1
}

# --- 0. checksum ----------------------------------------------------------
$hash = (Get-FileHash $ExePath -Algorithm SHA256).Hash
Write-Host "SHA-256: $hash"
if (Test-Path "god2iso.exe.sha256") {
    $expected = ((Get-Content "god2iso.exe.sha256")[0] -split "\s+")[0].ToUpper()
    Check "SHA-256 matches god2iso.exe.sha256" ($hash -eq $expected)
} else {
    Write-Host "  (no god2iso.exe.sha256 next to the exe - hash not compared)"
}

# --- 1. version -----------------------------------------------------------
Write-Host "`n[1] version" -ForegroundColor Yellow
& $ExePath --version | Out-Null
Check "--version exits 0" ($LASTEXITCODE -eq 0)
$ver = (& $ExePath --version 2>&1 | Out-String)
Check "--version reports 1.2.x" ($ver -match "1\.2\.")

# --- 2. audit (offline proof) ---------------------------------------------
Write-Host "`n[2] audit" -ForegroundColor Yellow
$aud = (& $ExePath audit 2>&1 | Out-String)
Check "audit exits 0" ($LASTEXITCODE -eq 0)
Check "audit verifies offline" ($aud -match "verified offline")

# --- 3. help --------------------------------------------------------------
Write-Host "`n[3] help" -ForegroundColor Yellow
$help = (& $ExePath --help 2>&1 | Out-String)
Check "--help shows convert" ($help -match "convert")
Check "--help shows extract" ($help -match "extract")

# --- 4. conversion (only if a GOD path was given) -------------------------
if ($GodPath -ne "") {
    Write-Host "`n[4] convert $GodPath" -ForegroundColor Yellow
    if ($OutDir -eq "") { $OutDir = Join-Path (Get-Location) "god2iso_test_out" }
    New-Item -ItemType Directory -Force -Path $OutDir | Out-Null
    $outIso = Join-Path $OutDir "test.iso"

    & $ExePath convert $GodPath -o $outIso --sha256
    Check "convert exits 0" ($LASTEXITCODE -eq 0)
    Check "output ISO exists" (Test-Path $outIso)

    if (Test-Path $outIso) {
        # --- 5. list ------------------------------------------------------
        Write-Host "`n[5] list $outIso" -ForegroundColor Yellow
        $lst = (& $ExePath list $outIso 2>&1 | Out-String)
        Check "list exits 0" ($LASTEXITCODE -eq 0)
        Check "default.xex present in ISO" ($lst -match "default\.xex")

        # --- 6. extract ---------------------------------------------------
        Write-Host "`n[6] extract" -ForegroundColor Yellow
        $exDir = Join-Path $OutDir "extracted"
        & $ExePath extract $outIso $exDir | Out-Null
        Check "extract exits 0" ($LASTEXITCODE -eq 0)
        Check "default.xex extracted to disk" (Test-Path (Join-Path $exDir "default.xex"))

        # --- 7. rebuild ---------------------------------------------------
        Write-Host "`n[7] rebuild" -ForegroundColor Yellow
        $rbIso = Join-Path $OutDir "rebuilt.iso"
        & $ExePath rebuild $outIso -o $rbIso | Out-Null
        Check "rebuild exits 0" ($LASTEXITCODE -eq 0)
        if (Test-Path $rbIso) {
            $lst2 = (& $ExePath list $rbIso 2>&1 | Out-String)
            Check "rebuilt ISO has default.xex" ($lst2 -match "default\.xex")
        }
    }
} else {
    Write-Host "`n[4-7] SKIPPED (no GOD package given)" -ForegroundColor Yellow
    Write-Host "  Pass -GodPath <folder-or-.live> to also test convert/list/extract/rebuild."
}

# --- 8. GUI launch (only when a display is available) ---------------------
Write-Host "`n[8] GUI launch" -ForegroundColor Yellow
try {
    $p = Start-Process $ExePath -PassThru -ErrorAction Stop
    Start-Sleep -Seconds 4
    if ($p.HasExited) {
        Check "GUI process stays running" $false
    } else {
        $w = (Get-Process -Id $p.Id).MainWindowTitle
        Check "GUI process stays running" $true
        Check "GUI window opens ('god2iso' in title)" ($w -match "god2iso")
        Stop-Process -Id $p.Id -Force
    }
} catch {
    Check "GUI launch attempt" $false
    Write-Host "  (GUI test skipped: $($_.Exception.Message))" -ForegroundColor DarkGray
}

# --- summary --------------------------------------------------------------
Write-Host ""
Write-Host "RESULT: $script:pass passed, $script:fail failed" -ForegroundColor Cyan
if ($script:fail -gt 0) { exit 1 } else { exit 0 }
