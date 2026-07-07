@echo off
setlocal
set "TASK_NAME=SATyS CRT Registros Nuevos 10am"
schtasks /Delete /TN "%TASK_NAME%" /F
if errorlevel 1 (
  echo No se pudo eliminar la tarea o no existia.
  exit /b 1
)
echo Tarea eliminada: %TASK_NAME%