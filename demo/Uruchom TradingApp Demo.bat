@echo off
chcp 65001 >nul
title TradingApp - wersja demonstracyjna
cd /d "%~dp0"
set DEMO=1
set "DATA_DIR=%~dp0dane"
set PANEL_HOST=127.0.0.1
set PANEL_PORT=8765
set PYTHONUTF8=1
set PYTHONDONTWRITEBYTECODE=

rem Juz dziala? Wtedy tylko otworz przegladarke.
powershell -NoProfile -Command "try{(New-Object Net.Sockets.TcpClient('127.0.0.1',8765)).Close(); exit 0}catch{exit 1}"
if %errorlevel%==0 (
  start "" "http://127.0.0.1:8765/"
  exit /b
)

echo.
echo   ==============================================================
echo     TradingApp - wersja demonstracyjna
echo   ==============================================================
echo.
echo   Panel otworzy sie sam w przegladarce:  http://127.0.0.1:8765
echo   Przy pierwszym uruchomieniu przygotowanie przykladowych danych
echo   trwa 1-3 minuty (pasek postepu jest na gorze strony).
echo.
echo   Aby WYLACZYC aplikacje - zamknij to okno.
echo.
start "" /b powershell -NoProfile -WindowStyle Hidden -Command "for($i=0;$i -lt 90;$i++){try{(New-Object Net.Sockets.TcpClient('127.0.0.1',8765)).Close(); Start-Process 'http://127.0.0.1:8765/'; break}catch{Start-Sleep 1}}"
"%~dp0python\python.exe" -m app.main
echo.
echo   Aplikacja zakonczyla dzialanie. Jesli to nie Ty ja zamknales, opis bledu jest powyzej
echo   oraz w pliku dane\tradingapp.log.
pause
