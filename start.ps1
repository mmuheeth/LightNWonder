#Requires -Version 5.1
<#
Starts the backend (FastAPI, port 8001) and frontend (Vite, port 3001) dev servers,
each in its own window via Start-Process.

The backend always starts elevated -- accept the UAC/Avecto prompt. It needs High
integrity to drive the i-deck and click the game: Windows UIPI drops input sent from
a lower integrity level to a higher one, and the whole cabinet (OledPanelSvc.exe and
the game) is auto-elevated to High here. Confirm from GET /api/ideck/status: "ready"
means the backend can drive the panel, "access_denied" means it cannot. See
docs/elevation.md for background.
#>

[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
$root = $PSScriptRoot

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

Write-Host 'Starting the backend elevated -- accept the UAC/Avecto prompt.'
# Its own elevated window by necessity. Start-Process joins -ArgumentList with
# spaces without quoting the parts, so the payload is wrapped in double quotes
# to stay ONE argument even if $root holds a space.
try {
    Start-Process -FilePath 'pwsh' -Verb RunAs -ArgumentList '-NoExit', '-Command', "`"$backendCommand`""
}
catch {
    throw "The backend was not started -- the elevation prompt was dismissed."
}

Write-Host 'Starting the frontend...'
Start-Process -FilePath 'pwsh' -ArgumentList '-NoExit', '-Command', $frontendCommand
