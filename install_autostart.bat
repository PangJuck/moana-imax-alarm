@echo off
chcp 65001 >nul
title CGV IMAX Alarm - 자동시작 등록
cd /d "%~dp0"
echo 로그온 시 자동 시작 작업을 등록합니다...
schtasks /Create /TN "CGV IMAX Alarm" /TR "wscript.exe \"%~dp0run_hidden.vbs\"" /SC ONLOGON /RL LIMITED /F
if errorlevel 1 ( echo [오류] 작업 등록 실패. & pause & exit /b 1 )
echo.
echo 절전/최대절전 진입 방지(AC 전원 기준)도 설정합니다...
powercfg /change standby-timeout-ac 0
powercfg /change hibernate-timeout-ac 0
echo.
echo [완료] PC가 켜지고 로그인되면 창 없이 자동 실행됩니다.
echo 지금 바로 시작하려면 아래 명령을 복사해 실행하세요:
echo     schtasks /Run /TN "CGV IMAX Alarm"
echo (해제하려면 uninstall_autostart.bat 실행)
pause
