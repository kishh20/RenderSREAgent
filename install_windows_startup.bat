@echo off
echo ============================================================
echo   Installing Render SRE Agent Windows Auto-Start Service
echo ============================================================
echo.

set "SCRIPT_PATH=%~dp0start_bot_silently.vbs"
set "STARTUP_FOLDER=%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup"
set "SHORTCUT_PATH=%STARTUP_FOLDER%\RenderSREAgent.vbs"

copy /y "%SCRIPT_PATH%" "%SHORTCUT_PATH%" >nul
if %errorlevel% equ 0 (
    echo [SUCCESS] SRE Agent installed to Windows Startup!
    echo Every time your PC turns on, the keep-alive pinger (every 4m)
    echo and real-time Telegram bot will run silently in the background.
    echo.
    echo Starting agent now...
    wscript "%SCRIPT_PATH%"
    echo [RUNNING] Agent is now running in the background.
) else (
    echo [ERROR] Failed to install to Startup folder.
)
echo.
pause
