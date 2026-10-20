@echo off
setlocal
call "%~dp0..\Starten.cmd" --collection courts %*
exit /b %errorlevel%
