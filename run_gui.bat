@echo off
setlocal EnableExtensions
cd /d "%~dp0"

set "LOG_FILE=%~dp0Caustiq_startup.log"
> "%LOG_FILE%" echo Caustiq startup log
>>"%LOG_FILE%" echo Date: %date% %time%
>>"%LOG_FILE%" echo Folder: %cd%

where py >nul 2>&1
if not errorlevel 1 goto :use_py

where python >nul 2>&1
if not errorlevel 1 goto :use_python

echo [ERROR] Python was not found.
>>"%LOG_FILE%" echo ERROR: Python was not found in PATH.
goto :failed

:use_py
py -3 startup_launcher.py %*
if errorlevel 1 goto :failed
goto :finished

:use_python
python startup_launcher.py %*
if errorlevel 1 goto :failed
goto :finished

:failed
echo.
echo [ERROR] Caustiq could not start.
echo Error log: %LOG_FILE%
echo.
type "%LOG_FILE%"
echo.
echo If packages are missing, run install_and_run.bat first.
pause
exit /b 1

:finished
endlocal
