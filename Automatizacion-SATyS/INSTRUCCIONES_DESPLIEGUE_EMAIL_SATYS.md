# Despliegue SATyS — Notificación por correo

Las credenciales y destinatarios se leen de `config/configuracion_local.json`.
No deben escribirse en `notificar_email.py` ni en variables de entorno.

## Permisos

```bash
cd /data/gustavo.garcia/satys/Automatizacion-SATyS
chmod 600 config/configuracion_local.json
```

## Prueba

```bash
/data/gustavo.garcia/satys/venv/bin/python notificar_email.py --test
```

## Riesgo aceptado

Las credenciales existentes se conservaron sin rotación por decisión operativa. La corrección elimina valores hardcodeados, pero no invalida copias anteriores del proyecto.
