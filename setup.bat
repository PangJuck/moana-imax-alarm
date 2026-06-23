@echo off
chcp 65001 >nul
title CGV IMAX Alarm - setup
cd /d "%~dp0"
call :findpy
if not defined PYEXE ( echo [오류] Python을 찾지 못했습니다. python.org 에서 설치 시 "Add python.exe to PATH" 를 체크하세요. & pause & exit /b 1 )
echo 사용 Python: %PYEXE%
echo.
echo [1/2] 필요한 라이브러리 설치(requests)...
%PYEXE% -m pip install -r "%~dp0requirements.txt"
echo.
echo [2/2] 점검: 현재 감시 영화의 IMAX 상태 1회 조회
%PYEXE% "%~dp0cgv_imax_alarm.py" check
echo.
echo 완료. 문제 없으면 run.bat 으로 실행하세요.
pause
exit /b

:findpy
set PYEXE=
where py >nul 2>nul && set "PYEXE=py -3"
if not defined PYEXE where python >nul 2>nul && set "PYEXE=python"
if not defined PYEXE if exist "%USERPROFILE%\anaconda3\python.exe" set "PYEXE=%USERPROFILE%\anaconda3\python.exe"
if not defined PYEXE if exist "C:\ProgramData\anaconda3\python.exe" set "PYEXE=C:\ProgramData\anaconda3\python.exe"
if not defined PYEXE if exist "C:\Anaconda3\python.exe" set "PYEXE=C:\Anaconda3\python.exe"
exit /b
