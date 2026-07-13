@echo off
setlocal EnableExtensions

REM AI Fun Motion — personal local launcher (Windows)
REM Binds to 127.0.0.1 only. Does not use --reload (reload interrupts in-process tasks).

cd /d "%~dp0"

if not exist ".venv\Scripts\activate.bat" (
  echo ERROR: Virtual environment not found at .venv
  echo Create it first:
  echo   python -m venv .venv
  echo   .venv\Scripts\activate
  echo   pip install -e ".[dev]"
  exit /b 1
)

call ".venv\Scripts\activate.bat"

if not exist ".env" (
  echo ERROR: .env file not found.
  echo Copy .env.example to .env and set WAVESPEED_API_KEY if needed:
  echo   copy .env.example .env
  exit /b 1
)

echo Starting AI Fun Motion on http://127.0.0.1:8000 ...
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000

endlocal
