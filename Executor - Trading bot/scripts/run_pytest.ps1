param(
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]] $PytestArgs
)

$ErrorActionPreference = "Stop"

$RepoRoot = Split-Path -Parent $PSScriptRoot
$TempRoot = Join-Path $RepoRoot ".tmp"
$PytestBaseTemp = Join-Path $TempRoot "pytest"

New-Item -ItemType Directory -Force -Path $TempRoot, $PytestBaseTemp | Out-Null

$env:TEMP = $TempRoot
$env:TMP = $TempRoot
$env:PYTHONDONTWRITEBYTECODE = "1"

if (-not $PytestArgs -or $PytestArgs.Count -eq 0) {
    $PytestArgs = @("tests")
}

$HasBaseTemp = $false
foreach ($Arg in $PytestArgs) {
    if ($Arg -eq "--basetemp" -or $Arg.StartsWith("--basetemp=")) {
        $HasBaseTemp = $true
        break
    }
}

if (-not $HasBaseTemp) {
    $PytestArgs += @("--basetemp", $PytestBaseTemp)
}

python -B -m pytest @PytestArgs
exit $LASTEXITCODE
