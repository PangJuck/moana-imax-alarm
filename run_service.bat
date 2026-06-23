@echo off
chcp 65001 >nul
set PYTHONUTF8=1
cd /d "%~dp0"
call :findpy
if not defined PYEXE ( echo [%date% %time%] Python not found >> "%~dp0bot.log" & exit /b 1 )
%PYEXE% "%~dp0cgv_imax_alarm.py" >> "%~dp0bot.log" 2>&1
exit /b

:findpy
set PYEXE=
where py >nul 2>nul && set "PYEXE=py -3"
if not defined PYEXE where python >nul 2>nul && set "PYEXE=python"
if not defined PYEXE if exist "%USERPROFILE%\anaconda3\python.exe" set "PYEXE=%USERPROFILE%\anaconda3\python.exe"
if not defined PYEXE if exist "C:\ProgramData\anaconda3\python.exe" set "PYEXE=C:\ProgramData\anaconda3\python.exe"
if not defined PYEXE if exist "C:\Anaconda3\python.exe" set "PYEXE=C:\Anaconda3\python.exe"
exit /b
