@echo off
@rem 启动或复用本地 OPL 服务。 / Start or reuse the local OPL server.
setlocal
cd /d "%~dp0" || exit /b 1
"%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe" -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\bootstrap.ps1" %*
set "OPL_EXIT=%ERRORLEVEL%"
if not "%OPL_EXIT%"=="0" if "%~1"=="" pause
exit /b %OPL_EXIT%
