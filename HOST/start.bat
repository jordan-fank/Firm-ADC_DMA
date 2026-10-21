@echo off
setlocal
cd /d "%~dp0"

set "PORT=8765"
set "URL=http://127.0.0.1:%PORT%/index.html"

rem If the bridge is already listening, just open the page.
netstat -ano | findstr ":%PORT% " | findstr "LISTENING" >nul 2>nul
if not errorlevel 1 goto open_page

rem Start the local serial bridge (pyserial + COM port list).
where python >nul 2>nul
if errorlevel 1 goto try_py
start "MCU Host Bridge - close this window to stop" /min python "%~dp0bridge.py"
goto wait_bridge

:try_py
where py >nul 2>nul
if errorlevel 1 goto no_python
start "MCU Host Bridge - close this window to stop" /min py -3 "%~dp0bridge.py"
goto wait_bridge

:no_python
echo.
echo Python 3 was not found.
echo Install Python 3 and make sure "python" is in PATH,
echo then run: python bridge.py
echo.
pause
exit /b 1

:wait_bridge
ping -n 3 127.0.0.1 >nul 2>nul

:open_page
if exist "%ProgramFiles(x86)%\Microsoft\Edge\Application\msedge.exe" goto edge_x86
if exist "%ProgramFiles%\Microsoft\Edge\Application\msedge.exe" goto edge_x64
if exist "%ProgramFiles%\Google\Chrome\Application\chrome.exe" goto chrome
start "" "%URL%"
goto done

:edge_x86
start "" "%ProgramFiles(x86)%\Microsoft\Edge\Application\msedge.exe" "%URL%"
goto done

:edge_x64
start "" "%ProgramFiles%\Microsoft\Edge\Application\msedge.exe" "%URL%"
goto done

:chrome
start "" "%ProgramFiles%\Google\Chrome\Application\chrome.exe" "%URL%"
goto done

:done
endlocal
