@echo off
chcp 65001 >nul
cd /d "%~dp0"
if not exist .venv (
  echo Tworze srodowisko Pythona...
  py -3 -m venv .venv || python -m venv .venv
  .venv\Scripts\python -m pip install --upgrade pip
  .venv\Scripts\python -m pip install -r requirements.txt
)
.venv\Scripts\python run.py %*
pause
