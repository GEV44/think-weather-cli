# One-command launcher for Windows PowerShell: creates .venv on first run, installs
# this project into it (no global changes), then runs `weather` with your arguments.
#   .\run.ps1 Yerevan --country AM --forecast
#   .\run.ps1 --demo
# Set $env:WEATHER_REINSTALL = "1" to reinstall after pulling new code.
# If scripts are blocked: powershell -ExecutionPolicy Bypass -File .\run.ps1 --demo
$ErrorActionPreference = "Stop"
Set-Location -LiteralPath $PSScriptRoot

$venvPython = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"
$weatherExe = Join-Path $PSScriptRoot ".venv\Scripts\weather.exe"

function Find-Python {
    $candidates = @(@("py", "-3"), @("python"), @("python3"))
    foreach ($candidate in $candidates) {
        $exe = $candidate[0]
        $extra = @($candidate | Select-Object -Skip 1)
        if (Get-Command $exe -ErrorAction SilentlyContinue) {
            & $exe @extra -c "import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)" 2>$null
            if ($LASTEXITCODE -eq 0) { return , $candidate }
        }
    }
    return $null
}

if (-not (Test-Path -LiteralPath $venvPython)) {
    $python = Find-Python
    if ($null -eq $python) {
        Write-Host "error: Python 3.11 or newer is required." -ForegroundColor Red
        Write-Host "hint: install it from https://www.python.org/downloads/ (tick 'Add python.exe to PATH')."
        exit 2
    }
    Write-Host "Creating virtual environment in .venv ..."
    $exe = $python[0]
    $extra = @($python | Select-Object -Skip 1)
    & $exe @extra -m venv .venv
    if ($LASTEXITCODE -ne 0) { Write-Host "error: could not create .venv" -ForegroundColor Red; exit 2 }
}

if (-not (Test-Path -LiteralPath $weatherExe) -or $env:WEATHER_REINSTALL -eq "1") {
    Write-Host "Installing think-weather-cli and its dependencies (first run only) ..."
    & $venvPython -m pip install --disable-pip-version-check --quiet .
    if ($LASTEXITCODE -ne 0) {
        Write-Host "error: dependency installation failed; check your internet connection." -ForegroundColor Red
        exit 2
    }
}

& $weatherExe @args
exit $LASTEXITCODE
