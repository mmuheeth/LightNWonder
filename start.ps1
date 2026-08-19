#Requires -Version 5.1
<#
Starts the backend (FastAPI, port 8001) and frontend (Vite, port 3001) dev servers.

The backend is launched *elevated* and the frontend is not, which is why this is
two windows rather than two tabs in one: Windows Terminal cannot mix integrity
levels inside a single window, so an elevated tab cannot join an unelevated one.

Why the backend needs elevation: the Virtual OLED i-deck is an SDL window owned by
OledPanelSvc.exe, which the cabinet's ProcessManager.exe launches elevated. Presses
are delivered by posting mouse messages to that window, and User Interface Privilege
Isolation drops input sent from a lower integrity level to a higher one -- even as
the same user. A medium-integrity backend therefore has every press silently
discarded, and GET /api/ideck/status reports "access_denied".

Pass -NoAdmin for a single window with two tabs. Everything except i-deck presses
works fine that way.
#>

[CmdletBinding()]
param(
    # Start the backend unelevated, in the same window as the frontend. i-deck
    # presses will be refused; OBS, OCR and event capture are unaffected.
    [switch]$NoAdmin
)

$ErrorActionPreference = 'Stop'
$root = $PSScriptRoot

if (-not (Get-Command wt -ErrorAction SilentlyContinue)) {
    throw "Windows Terminal (wt) is not on PATH. Install it, or start the two servers by hand -- see README.md."
}

# Depended on directly below instead of activating the venv, so check it here
# rather than letting pwsh report a missing relative path from inside a new tab.
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

# NO SEMICOLONS IN THESE TWO. Windows Terminal splits its tab commandline on ';'
# to separate tabs, and it does that *after* stripping the quotes PowerShell put
# on the argument -- so a "Set-Location x; npm run dev" payload has ' npm run dev'
# torn off and launched as an executable, which fails with 0x80070002. The working
# directory therefore comes from wt's own -d, and the venv is used by path rather
# than activated, which would have needed a second statement.
$backendTabCommand = '.\.venv\Scripts\python.exe -m app'
$frontendTabCommand = 'npm run dev'

if ($NoAdmin) {
    Write-Host 'Starting backend (unelevated -- i-deck presses will be refused) and frontend...'
    wt -w 0 new-tab --title 'backend' -d "$root\backend" pwsh -NoExit -Command $backendTabCommand `; new-tab --title 'frontend' -d "$root\frontend" pwsh -NoExit -Command $frontendTabCommand
    return
}

Write-Host 'Starting the backend elevated -- accept the UAC prompt.'
# Its own window by necessity: see the comment at the top of this file. Start-Process
# hands the payload to pwsh without re-parsing it, so unlike the wt tabs above this
# one may use a semicolon -- and it sets the working directory explicitly rather
# than trusting -WorkingDirectory to survive the elevation.
try {
    $elevatedCommand = "Set-Location '$root\backend'; .\.venv\Scripts\python.exe -m app"
    # Wrapped in double quotes so the payload stays ONE argument. Start-Process
    # joins -ArgumentList with spaces without quoting the parts, so an unquoted
    # payload reaches pwsh as five separate args -- which happens to work only
    # because -Command rejoins them, and stops working the moment $root holds a
    # space.
    Start-Process -FilePath 'pwsh' -Verb RunAs -ArgumentList '-NoExit', '-Command', "`"$elevatedCommand`""
}
catch {
    throw "The backend was not started -- the UAC prompt was dismissed. Re-run this script, or pass -NoAdmin to start it unelevated without i-deck presses."
}

Write-Host 'Starting the frontend...'
wt -w 0 new-tab --title 'frontend' -d "$root\frontend" pwsh -NoExit -Command $frontendTabCommand
