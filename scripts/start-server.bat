@echo off
setlocal
cd /d "%~dp0.."
if not exist downloads mkdir downloads

if defined PYTHON (
  set "PYTHON_CMD=%PYTHON%"
) else (
  where python >nul 2>nul
  if not errorlevel 1 (
    set "PYTHON_CMD=python"
  ) else (
    where py >nul 2>nul
    if not errorlevel 1 (
      set "PYTHON_CMD=py -3"
    ) else (
      echo [Aria2 Plus] Python not found. Please install Python 3.10+ first.
      exit /b 1
    )
  )
)

echo [Aria2 Plus] starting FastAPI server...
%PYTHON_CMD% -m server.run
