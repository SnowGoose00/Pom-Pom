param(
    [string[]]$PytestArgs = @("-v")
)

$ErrorActionPreference = "Stop"
$Root = Resolve-Path (Join-Path $PSScriptRoot "..")
$Python = Join-Path $Root ".venv\Scripts\python.exe"
$ResultDir = Join-Path $Root "test-results"
New-Item -ItemType Directory -Force -Path $ResultDir | Out-Null

$Ts = Get-Date -Format "yyyy-MM-dd_HHmmss"
$Log = Join-Path $ResultDir "pytest-full-$Ts.txt"
$Output = & $Python -m pytest @PytestArgs 2>&1 | Out-String
$Code = $LASTEXITCODE

[System.IO.File]::WriteAllText($Log, $Output, [System.Text.Encoding]::UTF8)
Copy-Item $Log (Join-Path $ResultDir "latest-pytest-full.txt") -Force

Write-Host ""
Write-Host "pytest exit code: $Code"
Write-Host "full log saved to: $Log"
Write-Host "summary json saved to: $ResultDir\latest-pytest.json"
exit $Code
