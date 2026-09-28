$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
if (Test-Path '.venv/Scripts/python.exe') { & '.venv/Scripts/python.exe' app.py } else { python app.py }
