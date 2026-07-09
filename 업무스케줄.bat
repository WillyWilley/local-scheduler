@echo off
rem 콘솔 창 없이 조용히 실행 (오류는 logs\app-*.log에 기록됨)
cd /d "%~dp0"
start "" pythonw app\schedule_web_editor.py
