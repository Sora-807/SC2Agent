@echo off
REM sc2Agent launcher - ONE window: API (127.0.0.1:8770) + Web (localhost:5273)
REM API runs in the background of this same console; vite runs in the foreground.
REM Close this window (or Ctrl+C) to stop BOTH services.
REM Web uses --strictPort: if 5273 is busy the window says so (no silent port drift).

cd /d "%~dp0"
title sc2Agent - API 8770 + Web 5273

REM open the browser a few seconds after boot, without an extra window
start "" /b cmd /c "timeout /t 6 /nobreak >nul && start "" http://localhost:5273"

REM API: background process sharing this console (output interleaves with vite's)
start "" /b uv run python -X utf8 tools\serve_api.py

REM Web: foreground - keeps this window alive
cd /d "%~dp0web"
pnpm dev --strictPort
