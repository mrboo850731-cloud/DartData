@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo ================================================================
echo   DartData backfill - keep this window open (closing = stop)
echo   progress: auto\output\backfill.log  /  backfill_status.json
echo ================================================================
echo.
py -3.12 -u auto\backfill.py --used 31000
echo.
echo ================================================================
echo   backfill finished. You can close this window.
echo ================================================================
pause
