@echo off
setlocal
cd /d "%~dp0"
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0packaging\rebuild_release.ps1"
if errorlevel 1 (
  echo.
  echo Build failed. See the error above.
  pause
  exit /b 1
)
echo.
echo Build completed successfully.
pause
