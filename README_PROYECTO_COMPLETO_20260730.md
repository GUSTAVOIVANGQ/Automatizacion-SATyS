# Automatización SATyS — proyecto completo 2026-07-30

Este paquete se construyó a partir de `automatizacion-satys-20260722-espera-robusta-120s.zip` y contiene las correcciones desplegadas el 30 de julio de 2026:

- extracción robusta y conciliación contra el total reportado por DataTables;
- cambio de la carpeta compartida a `/depi/dgp/DEI_DATOS/SATyS`;
- clasificación de folios `CORREO-2408` en `output/sin_operador_CORREO`;
- reconciliación global y sobrescritura controlada de las rutas automáticas de `TrámitesCRT.xlsx`;
- unidades `systemd` y corrección de instalación compatible con SELinux;
- 19 pruebas automatizadas.

## Archivos deliberadamente no incluidos

Por seguridad y para evitar sobrescribir producción, este ZIP no incluye:

- `config/configuracion_local.json` con credenciales reales (se incluye una versión con valores de marcador);
- `sesion_guardada.json`;
- el `TrámitesCRT.xlsx` productivo;
- logs, descargas, output ni marcadores de ejecución.

Antes de usar el proyecto en otro servidor, reemplace la configuración de marcadores con la configuración segura del servidor y copie el Excel maestro productivo. No reemplace el Excel productivo con una copia antigua.

La plantilla segura está en `config/configuracion_local.json.PLANTILLA`.

## Estado de la validación observada en producción

La corrida iniciada el 30 de julio de 2026 a las 17:07 confirmó la extracción íntegra de 1,420 registros únicos y comenzó a procesar 6 expedientes (1 nuevo y 5 reintentos de descarga incompleta).
