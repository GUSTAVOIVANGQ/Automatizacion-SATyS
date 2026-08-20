#!/usr/bin/env python3
"""Primitivas de guardado compatibles con archivos bind-mounted en Linux."""

from __future__ import annotations

import errno
import os
import shutil
from pathlib import Path


def reemplazar_desde_temporal(temporal: Path, destino: Path) -> bool:
    """Reemplaza destino; retorna True si EBUSY obligó a sobrescribir el inode.

    Un archivo montado individualmente dentro de un contenedor es un punto de
    montaje. Linux rechaza ``rename(2)`` sobre él con EBUSY aunque sea escribible.
    En ese único caso conservamos el inode montado y copiamos el temporal bajo el
    lock del llamador. Otros errores se propagan para no ocultar problemas reales.
    """
    temporal = Path(temporal)
    destino = Path(destino)
    try:
        os.replace(temporal, destino)
        return False
    except OSError as exc:
        if exc.errno != errno.EBUSY:
            raise

    with temporal.open("rb") as origen, destino.open("wb") as salida:
        shutil.copyfileobj(origen, salida, length=1024 * 1024)
        salida.flush()
        os.fsync(salida.fileno())
    temporal.unlink(missing_ok=True)
    return True
