#Requires -Version 5.1
<#
Starts the backend (FastAPI, port 8001) and frontend (Vite, port 3001) dev servers,
each in its own window via Start-Process.

IMPORTANT -- run this from the VS Code integrated terminal.

The backend needs High integrity to drive the i-deck and click the game (Windows
UIPI drops input sent from a lower integrity level to a higher one, and the whole
cabinet -- OledPanelSvc.exe and the game -- is auto-elevated to High here). On
these managed workstations the developer is a standard user with no "Run as
administrator", and Avecto does NOT auto-elevate python.exe. What it DOES
auto-elevate (silently, no prompt) is VS Code. A child process inherits its
parent's integrity, so a backend started from the elevated VS Code terminal
inherits High and the i-deck works -- with no prompt and no admin. That is exactly
how the sibling GameplayScript app works; nothing in either app's code bypasses
UIPI. See docs/elevation.md.

`Start-Process pwsh` (used below) creates a child of THIS shell, so run from the
elevated VS Code terminal the two servers inherit its High integrity. A Windows
Terminal (`wt`) tab is deliberately NOT used: a wt tab is hosted by the separate,
usually-Medium WindowsTerminal.exe broker rather than by your elevated shell, so it
would come up Medium and every i-deck press would be refused.

If you start this from a plain (Medium) window instead, the backend runs Medium and
only i-deck / game-input presses are refused (OBS, OCR, event capture, ROI, grid,
paylines all still work). GET /api/ideck/status reports "ready" when the backend
can drive the panel and "access_denied" when it cannot. To force elevation from a
plain window, use -Elevate.

-NoAdmin is accepted as a deprecated alias for the default.
#>

[CmdletBinding()]
param(
    # Force the backend elevated via a UAC/Avecto prompt (its own elevated window).
    # For when you are NOT starting from an already-elevated shell.
    [switch]$Elevate,

    # Deprecated: the default already starts the backend unelevated and lets it
    # inherit High from the (elevated) VS Code terminal it is launched from.
    [switch]$NoAdmin
)

$ErrorActionPreference = 'Stop'
$root = $PSScriptRoot

if ($NoAdmin) {
    Write-Warning '-NoAdmin is deprecated and has no effect: the default already starts the backend unelevated (it inherits integrity from the shell you run this in). Ignoring it.'
}

# Depended on directly instead of activating the venv, so check it here rather than
# letting pwsh report a missing relative path from inside a new window.
$venvPython = Join-Path $root 'backend\.venv\Scripts\python.exe'
if (-not (Test-Path $venvPython)) {
    throw "No virtualenv at $venvPython. Create it and install backend\requirements.txt -- see backend\README.md."
}

# A warning rather than a kill: something already listening on 8001 is far more
# likely to be a backend still in use than junk to clear away, and left alone the
# new process would only fail to bind with a less obvious message.
if (Get-NetTCPConnection -LocalPort 8001 -State Listen -ErrorAction SilentlyContinue) {
    Write-Warning "Port 8001 is already in use. Stop the running backend first, or the one this starts will fail to bind."
}

# Start-Process (unlike a wt tab) hands the payload to pwsh without re-parsing it,
# so the ';' between Set-Location and the launch is safe. `python -m app` must run
# from backend\, and the venv python is used by absolute path rather than activated.
$backendCommand = "Set-Location '$root\backend'; & '$venvPython' -m app"
$frontendCommand = "Set-Location '$root\frontend'; npm run dev"

if ($Elevate) {
    Write-Host 'Starting the backend elevated -- accept the UAC/Avecto prompt.'
    # Its own elevated window by necessity. Start-Process joins -ArgumentList with
    # spaces without quoting the parts, so the payload is wrapped in double quotes
    # to stay ONE argument even if $root holds a space.
    try {
        Start-Process -FilePath 'pwsh' -Verb RunAs -ArgumentList '-NoExit', '-Command', "`"$backendCommand`""
    }
    catch {
        throw "The backend was not started -- the elevation prompt was dismissed. Re-run, or start it from the elevated VS Code terminal instead (see docs/elevation.md)."
    }
    Write-Host 'Starting the frontend...'
    Start-Process -FilePath 'pwsh' -ArgumentList '-NoExit', '-Command', $frontendCommand
    return
}

# Default: backend and frontend each in their own pwsh window, inheriting the
# integrity of THIS shell. Run from the elevated VS Code terminal so the backend
# comes up High and the i-deck works; from a plain window it runs Medium and only
# i-deck/game-input presses are refused (GET /api/ideck/status then reports
# "access_denied" rather than "ready").
Write-Host 'Starting backend and frontend (each in its own window, inheriting this shell''s integrity).'
Write-Host 'For i-deck: run this from the elevated VS Code terminal. If presses are refused, see docs/elevation.md (or use -Elevate).'
Start-Process -FilePath 'pwsh' -ArgumentList '-NoExit', '-Command', $backendCommand
Start-Process -FilePath 'pwsh' -ArgumentList '-NoExit', '-Command', $frontendCommand
