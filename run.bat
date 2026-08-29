@echo off
rem Easiest way to run Ticket2PR. Double-click this file, or run it from a
rem terminal with extra flags, e.g.:  run.bat --dry-run
rem Defaults (which repo, which label) come from config.py.
"%~dp0venv\Scripts\python.exe" "%~dp0main.py" %*
pause
