@echo off
setlocal
cd /d "%~dp0"
where py >nul 2>nul || (
  echo Python 3.11+ is required: https://www.python.org/downloads/
  exit /b 1
)
py -3 -c "import sys; raise SystemExit(0 if sys.version_info >= (3,11) else 'Python 3.11+ is required')" || exit /b 1
py -3 -m venv .runtime-venv || exit /b 1
.runtime-venv\Scripts\python.exe -m pip install --no-index --find-links wheels --require-hashes -r runtime\requirements.lock || exit /b 1
.runtime-venv\Scripts\python.exe runtime\installer.py || exit /b 1
