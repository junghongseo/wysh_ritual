@echo off
chcp 65001 > nul
cls

echo ==================================================
echo     WYSH RITUAL AI 에디터 엔진을 가동합니다 
echo ==================================================
echo.

:: 가상환경 폴더가 있는지 확인하고 없으면 자동 생성 및 패키지 설치
IF NOT EXIST ".venv\Scripts\activate.bat" (
    echo [안내] 가상환환이 발견되지 않아 최초 1회 설정을 진행합니다. (약 1분 소요)
    python -m venv .venv
    call .venv\Scripts\activate.bat
    pip install -r requirements.txt
    echo 설정이 완료되었습니다!
    echo.
) ELSE (
    call .venv\Scripts\activate.bat
)

:: 메인 스크립트 실행
python ritual_engine.py

echo.
echo ==================================================
echo  생성이 완료되었습니다! 노션 대시보드를 확인해주세요.
echo ==================================================
echo.

pause
