@echo off
setlocal EnableExtensions

REM Development launcher with auto-reload. Do not use for long-running local tasks.
cd /d "%~dp0"
if not exist ".venv\Scripts\activate.bat" (
  echo ERROR: Virtual environment not found at .venv
  exit /b 1
)
call ".venv\Scripts\activate.bat"
if not exist ".env" (
  echo ERROR: .env file not found. Copy .env.example to .env first.
  exit /b 1
)
echo Starting AI Fun Motion DEV (reload) on http://127.0.0.1:8000 ...
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload
endlocal
