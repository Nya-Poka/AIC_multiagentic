param(
    [Parameter(Mandatory = $true)]
    [string]$BaseUrl,

    [ValidateSet('ports', 'paths')]
    [string]$Routing = 'ports'
)

$ErrorActionPreference = 'Stop'
$ProjectRoot = Split-Path -Parent $PSScriptRoot
. (Join-Path $PSScriptRoot 'import-env.ps1')
$env:PYTHONUTF8 = '1'
$env:PYTHONPATH = Join-Path $ProjectRoot 'src'
& (Join-Path $ProjectRoot '.venv\Scripts\python.exe') `
    (Join-Path $PSScriptRoot 'generate-acps.py') --base-url $BaseUrl --routing $Routing
