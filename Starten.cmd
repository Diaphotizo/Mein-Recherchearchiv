@echo off
"%~dp0runtime\pythonw.exe" -B "%~dp0archiv_start.py"  %*
exit /b %errorlevel%
