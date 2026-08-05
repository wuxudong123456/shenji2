@echo off
cd /d "%~dp0"
echo ============================================
echo  AuditWorkbench Backend
echo  http://127.0.0.1:5000
echo  Press Ctrl+C to stop.
echo ============================================
.venv\Scripts\python.exe app.py
echo.
echo Backend stopped.
pause
