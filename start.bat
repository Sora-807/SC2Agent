@echo off
REM sc2Agent launcher: backend API (127.0.0.1:8770) + frontend dev server (localhost:5273)
REM Both run in their own windows; close a window to stop that service.
REM Frontend uses --strictPort so the opened URL is always right.
REM If port 5273 is busy, close the other vite dev server first (or the web window shows the error).

cd /d "%~dp0"

start "sc2Agent API (8770)" cmd /k "uv run python -X utf8 tools\serve_api.py"
start "sc2Agent Web (5273)" cmd /k "cd /d "%~dp0web" && pnpm dev --strictPort"

REM give both a few seconds to boot, then open the browser
timeout /t 5 /nobreak >nul
start http://localhost:5273

echo.
echo Started:
echo   API  http://127.0.0.1:8770/api/health
echo   Web  http://localhost:5273
echo Close the two windows to stop.
echo.
