@echo off
setlocal
REM =============================================================
REM  Instala tarea diaria de Windows: SATyS CRT Registros Nuevos
REM  Uso:  instalar_tarea_diaria_satys.bat [HH:MM]
REM  Si no se pasa hora, se usa 10:00 por defecto.
REM  Ejecutar una sola vez desde la carpeta del proyecto.
REM =============================================================

set "PROJECT_DIR=%~dp0"
set "RUNNER=%PROJECT_DIR%ejecutar_monitor_registros.bat"
set "TASK_NAME=SATyS CRT Registros Nuevos 10am"

REM ── Hora: tomar el primer argumento o usar 10:00 ─────────────
if "%~1"=="" (
  set "HORA=10:00"
) else (
  set "HORA=%~1"
)

if not exist "%RUNNER%" (
  echo ERROR: No existe %RUNNER%
  echo Copia ejecutar_monitor_registros.bat en esta carpeta primero.
  exit /b 1
)

schtasks /Create /TN "%TASK_NAME%" /SC DAILY /ST %HORA% /TR "\"%RUNNER%\"" /RL LIMITED /F
if errorlevel 1 (
  echo.
  echo No se pudo crear la tarea. Prueba abrir esta consola como Administrador.
  exit /b 1
)

echo.
echo Tarea instalada: %TASK_NAME%
echo Hora: diario a las %HORA%
echo Comando: %RUNNER%
echo.
echo Para probarla ahora:
echo   schtasks /Run /TN "%TASK_NAME%"