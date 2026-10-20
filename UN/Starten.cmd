@echo off
setlocal
call "%~dp0..\Starten.cmd" --collection un %*
exit /b %errorlevel%
