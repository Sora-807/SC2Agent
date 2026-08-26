@echo off
REM sc2Agent eval runner (CLI quick path).
REM For the web UI (start.bat -> left rail "diag" -> "eval" page): same archive,
REM plus a run button with live progress.
REM Same LLM/.env as start.bat (loaded by vendor/agentic automatically).
REM With args (in cmd):
REM   run_eval.bat L1-gas-block           one scenario only
REM   run_eval.bat L1-gas-block --runs 1  one run (default 3 per scenario)
REM   run_eval.bat --tags live            filter by tag
REM Output: runtime\eval\<ts-dir>\report.md + index.jsonl.
REM Does NOT need the backend running - eval is an offline CLI.

cd /d "%~dp0"
title sc2Agent - eval

uv run python -X utf8 -m eval.run %*

echo.
echo ============================================================
echo done. records in runtime\eval\ - latest batch:
powershell -NoProfile -Command "Get-ChildItem -Directory runtime\eval | Sort-Object Name | Select-Object -Last 1 | ForEach-Object { $_.FullName; Get-ChildItem $_.FullName -Filter report.md -Recurse | Select-Object -First 1 -ExpandProperty FullName }"
echo (web view: start.bat, then left rail - diag - "eval" page)
echo ============================================================
pause
