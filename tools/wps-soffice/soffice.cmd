@echo off
rem LibreOffice-CLI-compatible shim backed by WPS Office COM.
rem Placed on PATH ahead of any real soffice so `shutil.which("soffice")` resolves here.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0wps-convert.ps1" %*
exit /b %ERRORLEVEL%
