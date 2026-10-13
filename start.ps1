# Starts backend (venv + uvicorn, :8000) and frontend (Vite, :5173) with one command:
#   .\start.ps1
# First run creates .venv and installs dependencies. Ctrl+C stops both.
$ErrorActionPreference = 'Stop'
Set-Location $PSScriptRoot

$venvPython = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
if (-not (Test-Path $venvPython)) {
    Write-Host '== creating .venv and installing backend dependencies'
    python -m venv .venv
    & $venvPython -m pip install -r requirements.txt -r requirements-dev.txt
}
if (-not (Test-Path 'src\frontend\node_modules')) {
    Write-Host '== installing frontend dependencies'
    npm --prefix src/frontend ci
}

Write-Host '== backend  http://localhost:8000/docs'
$backend = Start-Process -FilePath $venvPython -NoNewWindow -PassThru `
    -ArgumentList '-m', 'uvicorn', 'cinema.main:app', '--app-dir', 'src', '--reload'

try {
    Write-Host '== frontend http://localhost:5173  (Ctrl+C stops both)'
    npm --prefix src/frontend run dev
}
finally {
    if ($backend -and -not $backend.HasExited) {
        try { taskkill /PID $backend.Id /T /F 2>$null | Out-Null } catch { }
    }
}
