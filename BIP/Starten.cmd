@echo off
setlocal
call "%~dp0..\Starten.cmd" --collection bip %*
exit /b %errorlevel%
