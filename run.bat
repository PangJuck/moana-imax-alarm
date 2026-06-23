@echo off
chcp 65001 >nul
title CGV IMAX Alarm
set PYTHONUTF8=1
cd /d "%~dp0"
call :findpy
if not defined PYEXE ( echo [오류] Python을 찾지 못했습니다. python.org 에서 설치 시 "Add python.exe to PATH" 를 체크하세요. & pause & exit /b 1 )
%PYEXE% "%~dp0cgv_imax_alarm.py"
echo.
echo (프로그램이 종료되었습니다. 위 메시지를 확인하세요.)
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
