@echo off
@rem 读取新工作簿并同步数据库。 / Import new workbooks into the project database.
setlocal
cd /d "%~dp0" || exit /b 1
"%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe" -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\bootstrap.ps1" -SetupOnly
if errorlevel 1 goto failed
"%~dp0.venv\Scripts\python.exe" -X utf8 -m opl sync %*
if errorlevel 1 goto failed
pause
exit /b 0
:failed
pause
exit /b 1
