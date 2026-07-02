@echo off
setlocal
REM =============================================================
REM  SATyS CRT — Ejecutar monitor diario de registros nuevos
REM  Copia este .bat a la carpeta del proyecto junto con
REM  automatizar_registros_diario.py
REM =============================================================

set "PROJECT_DIR=%~dp0"
set "PYTHON_EXE=%PROJECT_DIR%python-3.11.9-embed-amd64\python.exe"
set "SCRIPT=%PROJECT_DIR%automatizar_registros_diario.py"

cd /d "%PROJECT_DIR%"

if not exist "%PYTHON_EXE%" (
  echo ERROR: No existe Python portable: %PYTHON_EXE%
  exit /b 1
)

if not exist "%SCRIPT%" (
  echo ERROR: No existe script monitor: %SCRIPT%
  exit /b 1
)

"%PYTHON_EXE%" "%SCRIPT%" --headless --workers 6
set "RC=%ERRORLEVEL%"
echo.
echo Monitor SATyS finalizo con codigo %RC%.
exit /b %RC%
