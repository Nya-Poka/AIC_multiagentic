$EnvFile = Join-Path (Split-Path -Parent $PSScriptRoot) '.env'
if (-not (Test-Path -LiteralPath $EnvFile)) {
    return
}

foreach ($Line in Get-Content -LiteralPath $EnvFile -Encoding utf8) {
    $Trimmed = $Line.Trim()
    if (-not $Trimmed -or $Trimmed.StartsWith('#')) {
        continue
    }
    $Parts = $Trimmed.Split('=', 2)
    if ($Parts.Count -ne 2) {
        throw "Invalid .env line: $Line"
    }
    $Name = $Parts[0].Trim()
    $Value = $Parts[1].Trim()
    if (($Value.StartsWith('"') -and $Value.EndsWith('"')) -or
        ($Value.StartsWith("'") -and $Value.EndsWith("'"))) {
        $Value = $Value.Substring(1, $Value.Length - 2)
    }
    Set-Item -LiteralPath "Env:$Name" -Value $Value
}
