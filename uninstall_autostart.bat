@echo off
chcp 65001 >nul
title CGV IMAX Alarm - 자동시작 해제
echo 실행 중인 작업을 중지하고 자동 시작을 해제합니다...
schtasks /End /TN "CGV IMAX Alarm" >nul 2>nul
schtasks /Delete /TN "CGV IMAX Alarm" /F
echo 완료.
pause
