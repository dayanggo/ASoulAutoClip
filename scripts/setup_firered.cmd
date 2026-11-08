@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0.."
if exist ".venv-firered\Scripts\python.exe" goto source
python -m venv .venv-firered
if errorlevel 1 goto failed
:source
if exist "third_party\FireRedASR2S\fireredasr2s" goto packages
git clone https://github.com/FireRedTeam/FireRedASR2S.git third_party/FireRedASR2S
if errorlevel 1 goto failed
git -C third_party/FireRedASR2S checkout --detach 4e7d9aaf4482a47cec1724807026b9b151926eb5
if errorlevel 1 goto failed
:packages
if exist "workspace\wheels\torch-2.7.1+cu126-cp312-cp312-win_amd64.whl" goto localtorch
".venv-firered\Scripts\python.exe" -m pip install torch==2.7.1+cu126 torchaudio==2.7.1+cu126 --index-url https://download.pytorch.org/whl/cu126
if errorlevel 1 goto failed
goto otherpackages
:localtorch
".venv-firered\Scripts\python.exe" -m pip install "workspace\wheels\torch-2.7.1+cu126-cp312-cp312-win_amd64.whl"
if errorlevel 1 goto failed
".venv-firered\Scripts\python.exe" -m pip install torchaudio==2.7.1+cu126 --index-url https://download.pytorch.org/whl/cu126
if errorlevel 1 goto failed
:otherpackages
".venv-firered\Scripts\python.exe" -m pip install -r requirements\firered.txt
if errorlevel 1 goto failed
".venv-firered\Scripts\python.exe" -c "import sys; sys.path.insert(0, 'third_party/FireRedASR2S'); from fireredasr2s.fireredasr2 import FireRedAsr2; import torch; print('FireRed ready; CUDA:', torch.cuda.is_available())"
if errorlevel 1 goto failed
echo Setup finished. Run run_clips.cmd to process your recording.
pause
exit /b 0
:failed
echo Setup failed. Review the error above and rerun to continue installation.
pause
exit /b 1
