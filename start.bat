@echo off
rem ============================================================
rem  ShiGuang Archives - one-click starter
rem  This file is ASCII-only on purpose: Chinese in .bat files
rem  gets garbled by cmd's default GBK parsing and can break
rem  the "python server.py" line. Chinese prompts are printed
rem  by Python instead.
rem ============================================================
title ShiGuang Archives - Local OCR Server
cd /d "%~dp0"
chcp 65001 >nul

echo.
echo  ========================================
echo   ShiGuang Archives - Local OCR Server
echo   Starting... please keep this window open
echo   Your browser will open automatically
echo  ========================================
echo.

rem --- Check 1: Python must be installed and on PATH ---
where python >nul 2>nul
if errorlevel 1 (
  echo [ERROR] Python was not found on this computer.
  echo Please install Python 3 first, then double-click this file again.
  echo Download: https://www.python.org/downloads/  ^(tick "Add to PATH"^)
  echo.
  pause
  exit /b 1
)

rem --- Check 2: server.py must sit in the same folder ---
if not exist "%~dp0server.py" (
  echo [ERROR] server.py was not found next to start.bat.
  echo Current folder: %~dp0
  echo Please keep start.bat and server.py in the same folder.
  echo.
  pause
  exit /b 1
)

rem --- Start the server (it runs until this window is closed) ---
python "%~dp0server.py"

echo.
echo Server has stopped.
pause
