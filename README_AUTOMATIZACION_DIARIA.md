# Automatización diaria SATyS — Registros nuevos

## Objetivo

Ejecutar todos los días a las 9:00 AM una revisión de la tabla **Documentos en Proceso** en SATyS, extraer todos los valores de la columna **Registro**, comparar contra `TrámitesCRT.xlsx` usando la columna con encabezado `1711` y procesar únicamente los registros nuevos con:

```bat
.\python-3.11.9-embed-amd64\python.exe main_procesar.py --archivo-registro registros.txt --headless --workers 6
```

## Archivos a copiar en el proyecto

Copia estos archivos dentro de:

```text
C:\Users\gustavo.garcia\Documents\GitHub\Automatizacion-SATyS
```

- `automatizar_registros_diario.py`
- `ejecutar_monitor_registros.bat`
- `instalar_tarea_diaria_satys.bat`
- `desinstalar_tarea_diaria_satys.bat`

## Primera prueba manual

Desde PowerShell o CMD en la carpeta del proyecto:

```bat
.\python-3.11.9-embed-amd64\python.exe automatizar_registros_diario.py --visible --workers 1
```

Si funciona, prueba en modo producción:

```bat
.\python-3.11.9-embed-amd64\python.exe automatizar_registros_diario.py --headless --workers 6
```

## Instalar tarea diaria 9:00 AM

Ejecuta una sola vez:

```bat
instalar_tarea_diaria_satys.bat
```

Para probar la tarea inmediatamente:

```bat
schtasks /Run /TN "SATyS CRT Registros Nuevos 9am"
```

## Salidas

El monitor genera:

- `registros.txt`: registros nuevos que consumirá `main_procesar.py`.
- `registros_diarios/registros_satys_YYYYMMDD_HHMMSS.txt`: todos los registros detectados en SATyS.
- `registros_diarios/registros_nuevos_YYYYMMDD_HHMMSS.txt`: solo los nuevos.
- `logs/monitor_registros_YYYYMMDD_HHMMSS.log`: salida de terminal completa.
- `logs/monitor_registros_YYYYMMDD_HHMMSS.json`: resumen estructurado.
- `logs/monitor_registros_ultimo.json`: último resultado.

## Notificaciones

El script intenta mostrar una notificación de Windows al finalizar. Para verla, conviene que la tarea se ejecute cuando tu sesión de Windows esté iniciada. Aunque no se muestre la notificación, siempre queda evidencia en `logs/`.
