@echo off
setlocal
cd /d "%~dp0"

rem Resolve OutputName from the project file so renaming does not break this script.
set "PROJ="
for %%F in ("%~dp0*.uvprojx") do if not defined PROJ set "PROJ=%%~fF"
if not defined PROJ (
    echo [size] project file not found
    exit /b 1
)

set "OUTNAME="
for /f "tokens=3 delims=<>" %%N in ('findstr /c:"<OutputName>" "%PROJ%"') do if not defined OUTNAME set "OUTNAME=%%N"
if not defined OUTNAME (
    echo [size] OutputName not found
    exit /b 1
)

copy /y "..\OBJ\%OUTNAME%.map" ".\%OUTNAME%.map" >nul
if errorlevel 1 (
    echo [size] map not found: ..\OBJ\%OUTNAME%.map
    exit /b 1
)

.\keil5_disp_size_bar.exe
del ".\%OUTNAME%.map" >nul 2>nul
exit /b 0
