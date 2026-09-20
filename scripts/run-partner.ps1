param(
    [Parameter(Mandatory = $true)]
    [ValidateSet('literature', 'experiment', 'analysis', 'review')]
    [string]$Agent,

    [int]$Port = 0
)

$ErrorActionPreference = 'Stop'
$ProjectRoot = Split-Path -Parent $PSScriptRoot
. (Join-Path $PSScriptRoot 'import-env.ps1')
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

$env:RESEARCH_MESH_PORT = [string]$Port
& (Join-Path $ProjectRoot '.venv\Scripts\python.exe') -m research_mesh.partner_service
