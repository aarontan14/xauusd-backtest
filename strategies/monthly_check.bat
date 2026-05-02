@echo off
REM Wrapper for Windows Task Scheduler. Logs to logs\schtasks_run.log.
REM Hardcoded python path so scheduled (non-interactive) sessions find it.
cd /d "C:\Users\aaron\Downloads\BackTest Engine"
if not exist logs mkdir logs
echo === run started at %DATE% %TIME% === >> "logs\schtasks_run.log"
"C:\Python314\python.exe" "strategies\monthly_check.py" >> "logs\schtasks_run.log" 2>&1
echo === run finished at %DATE% %TIME% (exit %ERRORLEVEL%) === >> "logs\schtasks_run.log"
