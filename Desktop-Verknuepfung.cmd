@echo off
setlocal
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0Desktop-Verknuepfung.ps1"
set "archive_exit=%errorlevel%"
echo.
pause
exit /b %archive_exit%
