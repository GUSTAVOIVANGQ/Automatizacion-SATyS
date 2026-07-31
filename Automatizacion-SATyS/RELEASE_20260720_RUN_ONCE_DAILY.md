# SATyS - Release 2026.07.20-run-once-daily.1

Corrección enfocada en evitar corridas y correos repetidos:

- elimina `Restart=on-failure` del servicio diario;
- usa `Restart=no` y horario estricto a las 01:00;
- cambia el timer a `Persistent=false`;
- agrega guarda atómica por fecha en `runs/daily_guard/`;
- conserva override técnico mediante `SATYS_FORCE_RUN=1`;
- actualiza el generador de timers de la UI para no restaurar `Persistent=true`;
- incluye prueba automatizada del comportamiento de una corrida por fecha;
- incluye script de aplicación rápida sin reinstalar dependencias.
