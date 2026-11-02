#!/bin/bash
# Mac 설치 파일. 더블클릭하면 프로그램을 설치하고 helper 명령과 바로가기를 만든다.

cd "$(dirname "$0")" || exit 1

finish() {
    echo
    read -r -p "Enter를 누르면 창이 닫힙니다."
    exit "$1"
}

if ! command -v python3.12 >/dev/null 2>&1; then
    echo "Python 3.12가 없습니다. README의 1단계를 먼저 진행해 주세요."
    finish 1
fi

echo "설치 중입니다. 몇 분 걸릴 수 있습니다."
python3.12 -m venv .venv || finish 1
.venv/bin/python -m pip install --disable-pip-version-check -r requirements.txt || finish 1
.venv/bin/python scripts/setup_launcher.py || finish 1

echo
echo "설치가 끝났습니다."
echo "바탕화면의 zipup_helper를 더블클릭하거나, 터미널을 새로 열고 helper를 입력하세요."
finish 0
