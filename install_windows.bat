@echo off
rem Windows 설치 파일. 더블클릭하면 프로그램을 설치하고 helper 명령과 바로가기를 만든다.
chcp 65001 >nul
cd /d "%~dp0"

py -3.12 --version >nul 2>nul
if errorlevel 1 (
    echo Python 3.12가 없습니다. README의 1단계를 먼저 진행해 주세요.
    goto failed
)

echo 설치 중입니다. 몇 분 걸릴 수 있습니다.
py -3.12 -m venv .venv
if errorlevel 1 goto failed
.venv\Scripts\python -m pip install --disable-pip-version-check -r requirements.txt
if errorlevel 1 goto failed
.venv\Scripts\python scripts\setup_launcher.py
if errorlevel 1 goto failed

echo.
echo 설치가 끝났습니다.
echo 바탕화면의 zipup_helper를 더블클릭하거나, PowerShell을 새로 열고 helper를 입력하세요.
if not "%~1"=="nopause" pause
exit /b 0

:failed
echo.
echo 설치하지 못했습니다. 위의 메시지를 캡처해서 관리자에게 보내 주세요.
if not "%~1"=="nopause" pause
exit /b 1
