@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0.."
if not exist "config\local" mkdir "config\local"
if exist "config\local\highlights.json" goto configready
if exist "config\highlights.local.json" (
    copy "config\highlights.local.json" "config\local\highlights.json" >nul
) else (
    copy "config\examples\highlights.example.json" "config\local\highlights.json" >nul
)
:configready
if not exist ".env" copy ".env.example" ".env" >nul

if exist ".venv\Scripts\python.exe" goto dependencies
echo Creating local Python environment...
python -m venv .venv
if errorlevel 1 goto failed

:dependencies
".venv\Scripts\python.exe" -X utf8 -c "import requests, faster_whisper, av, jieba; assert faster_whisper.__version__ == '1.2.1'; assert int(av.__version__.split('.')[0]) < 19" >nul 2>&1
if not errorlevel 1 goto run
echo Installing clipping dependencies. This may take a few minutes...
".venv\Scripts\python.exe" -m pip install -r requirements\asr.txt
if errorlevel 1 goto failed

:run
".venv\Scripts\python.exe" -X utf8 -m core.cli %*
set "CLIP_EXIT=%ERRORLEVEL%"
echo.
if "%CLIP_EXIT%"=="0" (echo Finished. See the output path above.) else (echo Not completed. Review the messages above.)
pause
exit /b %CLIP_EXIT%

:failed
echo Setup failed. Check Python 3.10+ and network access, then run again.
pause
exit /b 1
