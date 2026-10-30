#!/usr/bin/env sh
# One-command launcher for macOS/Linux: creates .venv on first run, installs this
# project into it (no global changes), then runs `weather` with your arguments.
#   ./run.sh Yerevan --country AM --forecast
#   ./run.sh --demo
# Set WEATHER_REINSTALL=1 to reinstall after pulling new code.
set -eu

cd "$(dirname "$0")"
VENV=".venv"

find_python() {
    for candidate in python3 python; do
        if command -v "$candidate" >/dev/null 2>&1 &&
            "$candidate" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)' 2>/dev/null; then
            echo "$candidate"
            return 0
        fi
    done
    return 1
}

if [ ! -x "$VENV/bin/python" ]; then
    PYTHON="$(find_python)" || {
        echo "error: Python 3.11 or newer is required (python3 --version)." >&2
        echo "hint: install it from https://www.python.org/downloads/ and re-run ./run.sh" >&2
        exit 2
    }
    echo "Creating virtual environment in $VENV ..." >&2
    "$PYTHON" -m venv "$VENV" || {
        echo "error: could not create a virtual environment (on Debian/Ubuntu: apt install python3-venv)." >&2
        exit 2
    }
fi

if [ ! -x "$VENV/bin/weather" ] || [ "${WEATHER_REINSTALL:-0}" = "1" ]; then
    echo "Installing think-weather-cli and its dependencies (first run only) ..." >&2
    "$VENV/bin/python" -m pip install --disable-pip-version-check --quiet . || {
        echo "error: dependency installation failed; check your internet connection." >&2
        exit 2
    }
fi

exec "$VENV/bin/weather" "$@"
