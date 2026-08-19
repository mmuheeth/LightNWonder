#Requires -Version 5.1
<#
Starts the backend (FastAPI, port 8001) and frontend (Vite, port 3001) dev servers
as two tabs in a single Windows Terminal window.
#>

$root = $PSScriptRoot

wt -w 0 new-tab --title "backend" pwsh -NoExit -Command "Set-Location `"$root\backend`"; .\.venv\Scripts\Activate.ps1; python -m app" `; new-tab --title "frontend" pwsh -NoExit -Command "Set-Location `"$root\frontend`"; npm run dev"
