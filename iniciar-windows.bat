@echo off
setlocal EnableExtensions
cd /d "%~dp0"
title YouTube Music ZimaOS - Inicializador Windows
color 0B
if not exist "%~dp0iniciar-windows.ps1" (
  echo ERRO: iniciar-windows.ps1 nao foi encontrado nesta pasta.
  pause
  exit /b 1
)
echo =====================================================
echo  YOUTUBE MUSIC ZIMAOS - WINDOWS
echo =====================================================
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0iniciar-windows.ps1"
set "RET=%ERRORLEVEL%"
echo.
if not "%RET%"=="0" echo O inicializador terminou com codigo %RET%.
echo Esta janela ficara aberta ate voce pressionar uma tecla.
pause
exit /b %RET%
