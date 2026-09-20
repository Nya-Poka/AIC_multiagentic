$ErrorActionPreference = 'Stop'
$ProjectRoot = Split-Path -Parent $PSScriptRoot
$env:PYTHONUTF8 = '1'
$env:PYTHONPATH = Join-Path $ProjectRoot 'src'
& (Join-Path $ProjectRoot '.venv\Scripts\python.exe') -m uvicorn research_mesh.api:app --host 127.0.0.1 --port 8000
