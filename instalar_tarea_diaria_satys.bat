@echo off
setlocal
REM =============================================================
REM  Instala tarea diaria de Windows: SATyS CRT Registros Nuevos 9am
REM  Ejecutar una sola vez desde la carpeta del proyecto.
REM =============================================================

set "PROJECT_DIR=%~dp0"
set "RUNNER=%PROJECT_DIR%ejecutar_monitor_registros.bat"
set "TASK_NAME=SATyS CRT Registros Nuevos 9am"

if not exist "%RUNNER%" (
  echo ERROR: No existe %RUNNER%
  echo Copia ejecutar_monitor_registros.bat en esta carpeta primero.
  exit /b 1
)

schtasks /Create /TN "%TASK_NAME%" /SC DAILY /ST 09:00 /TR "\"%RUNNER%\"" /RL LIMITED /F
if errorlevel 1 (
  echo.
  echo No se pudo crear la tarea. Prueba abrir esta consola como Administrador.
  exit /b 1
)

echo.
echo Tarea instalada: %TASK_NAME%
echo Hora: diario a las 09:00
echo Comando: %RUNNER%
echo.
echo Para probarla ahora:
echo   schtasks /Run /TN "%TASK_NAME%"
