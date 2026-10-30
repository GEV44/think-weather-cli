@echo off
rem One-command launcher for Windows CMD: creates .venv on first run, installs this
rem project into it (no global changes), then runs `weather` with your arguments.
rem   run.bat Yerevan --country AM --forecast
rem   run.bat --demo
rem Set WEATHER_REINSTALL=1 to reinstall after pulling new code.
setlocal
cd /d "%~dp0"

if exist ".venv\Scripts\python.exe" goto install

set "PYTHON="
py -3 -c "import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)" >nul 2>&1 && set "PYTHON=py -3"
if not defined PYTHON (
    python -c "import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)" >nul 2>&1 && set "PYTHON=python"
)
if not defined PYTHON (
    echo error: Python 3.11 or newer is required. 1>&2
    echo hint: install it from https://www.python.org/downloads/ ^(tick "Add python.exe to PATH"^). 1>&2
    exit /b 2
)
echo Creating virtual environment in .venv ...
%PYTHON% -m venv .venv || (echo error: could not create .venv 1>&2 & exit /b 2)

:install
if "%WEATHER_REINSTALL%"=="1" goto doinstall
if exist ".venv\Scripts\weather.exe" goto run
:doinstall
echo Installing think-weather-cli and its dependencies (first run only) ...
".venv\Scripts\python.exe" -m pip install --disable-pip-version-check --quiet . || (
    echo error: dependency installation failed; check your internet connection. 1>&2
    exit /b 2
)

:run
".venv\Scripts\weather.exe" %*
exit /b %ERRORLEVEL%
