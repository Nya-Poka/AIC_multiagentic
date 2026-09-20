param(
    [Parameter(Mandatory = $true)]
    [ValidateSet('literature', 'experiment', 'analysis', 'review')]
    [string]$Agent,

    [int]$Port = 0
)

$ErrorActionPreference = 'Stop'
$ProjectRoot = Split-Path -Parent $PSScriptRoot
$env:PYTHONUTF8 = '1'
$env:PYTHONPATH = Join-Path $ProjectRoot 'src'
$env:RESEARCH_MESH_PARTNER = $Agent

if ($Port -eq 0) {
    $Port = switch ($Agent) {
        'literature' { 8011 }
        'experiment' { 8012 }
        'analysis' { 8013 }
        'review' { 8014 }
    }
}

& (Join-Path $ProjectRoot '.venv\Scripts\python.exe') -m uvicorn research_mesh.partner_service:app --host 127.0.0.1 --port $Port
