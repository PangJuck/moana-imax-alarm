@echo off
chcp 65001 >nul
set PYTHONUTF8=1
cd /d "%~dp0"
where pyw >nul 2>nul && ( start "" pyw -3 "%~dp0gui.py" & exit /b )
where pythonw >nul 2>nul && ( start "" pythonw "%~dp0gui.py" & exit /b )
where py >nul 2>nul && ( start "" py -3 "%~dp0gui.py" & exit /b )
where python >nul 2>nul && ( start "" python "%~dp0gui.py" & exit /b )
echo Python을 찾지 못했습니다. python.org 에서 설치(Add python.exe to PATH 체크) 후 다시 실행하세요.
pause
