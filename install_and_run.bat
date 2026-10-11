@echo off
setlocal EnableExtensions
cd /d "%~dp0"

set "LOG_FILE=%~dp0Caustiq_startup.log"
> "%LOG_FILE%" echo Caustiq startup log
>>"%LOG_FILE%" echo Date: %date% %time%
>>"%LOG_FILE%" echo Folder: %cd%

echo.
echo ==============================================================
echo   Caustiq v2.4.2 - Beam Profiling ^& M2 Analysis
echo ==============================================================
echo.

where py >nul 2>&1
if not errorlevel 1 (
    set "PY_CMD=py -3"
    goto :python_found
)

where python >nul 2>&1
if not errorlevel 1 (
    set "PY_CMD=python"
    goto :python_found
)

echo [ERROR] Python was not found.
>>"%LOG_FILE%" echo ERROR: Python was not found in PATH.
echo.
echo Install Python 3.10 or newer from https://www.python.org/downloads/
echo During installation, enable "Add python.exe to PATH".
goto :failed

:python_found
echo [1/5] Checking Python...
%PY_CMD% -c "import sys; print('      Python', sys.version.split()[0]); raise SystemExit(0 if sys.version_info.__ge__((3,10)) else 1)"
if errorlevel 1 goto :old_python

%PY_CMD% -m pip --version >nul 2>&1
if errorlevel 1 (
    echo       pip was not found; enabling it now...
    %PY_CMD% -m ensurepip --upgrade
    if errorlevel 1 goto :install_failed
)

%PY_CMD% -u install_dependencies.py --log "%LOG_FILE%"
if errorlevel 1 goto :install_failed

echo.
echo [4/5] Verifying the runtime...
%PY_CMD% -c "import numpy, pandas, scipy, matplotlib, PIL, PySide6; print('      Dependencies verified successfully.')"
if errorlevel 1 goto :verify_failed

echo [5/5] Starting GUI...
%PY_CMD% startup_launcher.py %*
if errorlevel 1 goto :gui_failed
goto :finished

:old_python
echo [ERROR] Python 3.10 or newer is required.
>>"%LOG_FILE%" echo ERROR: Python is older than 3.10.
goto :failed

:install_failed
echo.
echo [ERROR] Package installation failed.
echo Check the visible message above and this log:
echo %LOG_FILE%
goto :failed

:verify_failed
echo.
echo [ERROR] Installation completed, but one or more packages cannot be imported.
echo Details are saved in:
echo %LOG_FILE%
goto :failed

:gui_failed
echo.
echo [ERROR] The GUI could not start.
echo Details are saved in:
echo %LOG_FILE%
goto :failed

:failed
echo.
echo -------------------- startup log --------------------
type "%LOG_FILE%"
echo -----------------------------------------------------
echo.
pause
exit /b 1

:finished
echo.
echo Caustiq closed normally.
endlocal
