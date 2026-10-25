@echo off
set "STARTUP_FOLDER=%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup"
set "SHORTCUT_PATH=%STARTUP_FOLDER%\RenderSREAgent.vbs"

if exist "%SHORTCUT_PATH%" (
    del /f /q "%SHORTCUT_PATH%"
    echo [REMOVED] RenderSREAgent removed from Windows Startup.
) else (
    echo [INFO] Not currently installed in Startup.
)
pause
