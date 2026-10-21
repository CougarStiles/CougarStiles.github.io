@echo off
REM Double-click to start the portfolio preview server.
REM Opens http://localhost:8234/index.html and reloads on save.
cd /d "%~dp0"
title Portfolio preview - close this window to stop
python serve.py
pause
