@echo off
setlocal
cd /d "%~dp0"
where py >nul 2>nul
if errorlevel 1 (
  set "DUEWISE_PYTHON=python"
) else (
  set "DUEWISE_PYTHON=py -3.12"
)
if not exist ".venv\Scripts\python.exe" goto setup
if not exist "apps\desktop\node_modules\electron\cli.js" goto setup
goto launch
:setup
%DUEWISE_PYTHON% scripts\setup.py --demo
if errorlevel 1 goto failed
:launch
%DUEWISE_PYTHON% scripts\run_local.py --demo
if errorlevel 1 goto failed
exit /b 0
:failed
echo.
echo Duewise did not start. Read the message above. Python 3.12 and Node.js 24 LTS are required.
pause
exit /b 1
