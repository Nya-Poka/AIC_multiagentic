$ErrorActionPreference = 'Stop'
$ProjectRoot = Split-Path -Parent $PSScriptRoot
. (Join-Path $PSScriptRoot 'import-env.ps1')
$env:PYTHONUTF8 = '1'
$env:PYTHONPATH = Join-Path $ProjectRoot 'src'
& (Join-Path $ProjectRoot '.venv\Scripts\python.exe') -m uvicorn research_mesh.llm_service:app --host 127.0.0.1 --port 8020
