#!/usr/bin/env python3
"""Carga la configuración local del servidor desde un único archivo JSON.

Las credenciales no se leen de variables de entorno ni quedan escritas en los
módulos del programa. El archivo esperado es:

    config/configuracion_local.json

Debe mantenerse con permisos 600 en el servidor.
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

PROJECT_DIR = Path(__file__).resolve().parent
CONFIG_FILE = PROJECT_DIR / "config" / "configuracion_local.json"


class ConfiguracionError(RuntimeError):
    """Configuración local ausente o inválida."""


@lru_cache(maxsize=1)
def cargar_configuracion() -> dict[str, Any]:
    if not CONFIG_FILE.exists():
        raise ConfiguracionError(
            f"No existe el archivo de configuración local: {CONFIG_FILE}. "
            "Crea el archivo a partir de config/configuracion_local.example.json."
        )
    try:
        data = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
    except Exception as exc:
        raise ConfiguracionError(f"No se pudo leer {CONFIG_FILE}: {exc}") from exc
    if not isinstance(data, dict):
        raise ConfiguracionError(f"La raíz de {CONFIG_FILE} debe ser un objeto JSON.")
    return data


def _seccion(nombre: str) -> dict[str, Any]:
    value = cargar_configuracion().get(nombre, {})
    return value if isinstance(value, dict) else {}


def credenciales_satys() -> tuple[str, str]:
    cfg = _seccion("satys")
    usuario = str(cfg.get("usuario", "")).strip()
    password = str(cfg.get("password", "")).strip()
    return usuario, password


def configuracion_email() -> dict[str, Any]:
    return dict(_seccion("gmail"))


def configuracion_procesamiento() -> dict[str, Any]:
    return dict(_seccion("procesamiento"))


def ruta_configurada(clave: str, default: str | Path) -> Path:
    rutas = _seccion("rutas")
    valor = str(rutas.get(clave, default)).strip()
    path = Path(valor or default)
    if not path.is_absolute():
        path = PROJECT_DIR / path
    return path


def carpeta_compartida() -> Path:
    return ruta_configurada("carpeta_compartida", "/depi/DEI_DATOS/SATyS")
