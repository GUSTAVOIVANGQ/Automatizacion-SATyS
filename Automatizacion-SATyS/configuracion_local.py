#!/usr/bin/env python3
"""Configuración portable de SATyS.

Orden de precedencia:
1. Variables de entorno ``SATYS_*`` (útiles en contenedores/CI).
2. ``config/configuracion_local.json`` (o ``SATYS_CONFIG_FILE``).
3. Valores por defecto portables relativos al proyecto.

El archivo ``.env`` de la raíz se carga al importar este módulo para que el
mismo mecanismo funcione en Windows, macOS, Linux, Docker/Podman y venv.

NOTA DE SEGURIDAD — CREDENCIALES EN TEXTO PLANO:
Las credenciales de SATyS y de Gmail se almacenan directamente en
config/configuracion_local.json. Esto es aceptado deliberadamente porque
el proyecto se ejecuta ÚNICAMENTE en entornos aislados y controlados por
nuestro servidor (red interna CRT / DEPI). El archivo nunca se expone a
redes públicas ni se sube a repositorios remotos (.gitignore lo excluye).
Si en el futuro el despliegue cambia, migrar las credenciales a variables
de entorno (SATYS_USUARIO, SATYS_PASSWORD, SATYS_EMAIL_APP_PASSWORD, etc.)
usando el mecanismo de precedencia ya implementado en este módulo.
"""

from __future__ import annotations

import json
import os
from functools import lru_cache
from pathlib import Path
from typing import Any

PROJECT_DIR = Path(__file__).resolve().parent


def _cargar_dotenv_local() -> None:
    """Carga .env sin convertirlo en una dependencia obligatoria."""
    env_file = PROJECT_DIR / ".env"
    if not env_file.exists():
        return
    try:
        from dotenv import load_dotenv  # type: ignore

        load_dotenv(env_file, override=False)
        return
    except Exception:
        pass

    try:
        for raw in env_file.read_text(encoding="utf-8-sig").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            key = key.strip()
            value = value.strip().strip("'\"")
            if key:
                os.environ.setdefault(key, value)
    except OSError:
        return


_cargar_dotenv_local()

CONFIG_FILE = Path(
    os.getenv(
        "SATYS_CONFIG_FILE",
        str(PROJECT_DIR / "config" / "configuracion_local.json"),
    )
).expanduser()


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
        data = json.loads(CONFIG_FILE.read_text(encoding="utf-8-sig"))
    except Exception as exc:
        raise ConfiguracionError(f"No se pudo leer {CONFIG_FILE}: {exc}") from exc
    if not isinstance(data, dict):
        raise ConfiguracionError(f"La raíz de {CONFIG_FILE} debe ser un objeto JSON.")
    return data


def _seccion(nombre: str) -> dict[str, Any]:
    value = cargar_configuracion().get(nombre, {})
    return value if isinstance(value, dict) else {}


def _env_texto(nombre: str) -> str | None:
    value = os.getenv(nombre)
    if value is None:
        return None
    value = value.strip()
    return value if value else None


def credenciales_satys() -> tuple[str, str]:
    cfg = _seccion("satys")
    usuario = _env_texto("SATYS_USUARIO") or str(cfg.get("usuario", "")).strip()
    password = _env_texto("SATYS_PASSWORD") or str(cfg.get("password", "")).strip()
    return usuario, password


def configuracion_email() -> dict[str, Any]:
    cfg = dict(_seccion("gmail"))
    overrides = {
        "remitente": "SATYS_EMAIL_REMITENTE",
        "app_password": "SATYS_EMAIL_APP_PASSWORD",
        "from_name": "SATYS_EMAIL_FROM_NAME",
    }
    for key, env_name in overrides.items():
        value = _env_texto(env_name)
        if value is not None:
            cfg[key] = value
    enabled = _env_texto("SATYS_EMAIL_ENABLED")
    if enabled is not None:
        cfg["enabled"] = enabled.lower() in {"1", "true", "yes", "si", "sí"}
    return cfg


_PROCESAMIENTO_ENV_ENTERO = {
    "workers": "SATYS_WORKERS",
    "internos_workers": "SATYS_INTERNOS_WORKERS",
    "timeout_registro": "SATYS_TIMEOUT_REGISTRO",
    "reintentos_registro": "SATYS_REINTENTOS_REGISTRO",
    "workers_reintento": "SATYS_WORKERS_REINTENTO",
    "reintentos_extraccion": "SATYS_REINTENTOS_EXTRACCION",
    "timeout_tabla": "SATYS_TIMEOUT_TABLA",
    "espera_reintento_extraccion": "SATYS_ESPERA_REINTENTO_EXTRACCION",
}


def configuracion_procesamiento() -> dict[str, Any]:
    cfg = dict(_seccion("procesamiento"))
    for clave, env_name in _PROCESAMIENTO_ENV_ENTERO.items():
        valor = _env_texto(env_name)
        if valor is None:
            continue
        try:
            cfg[clave] = int(valor)
        except ValueError as exc:
            raise ConfiguracionError(
                f"{env_name} debe ser un entero; valor recibido: {valor!r}."
            ) from exc
    return cfg


_RUTA_ENV = {
    "descargas": "SATYS_DESCARGAS_DIR",
    "output": "SATYS_OUTPUT_DIR",
    "excel": "SATYS_EXCEL_PATH",
    "carpeta_compartida": "SATYS_SHARED_DIR",
}


def ruta_configurada(clave: str, default: str | Path) -> Path:
    rutas = _seccion("rutas")
    env_name = _RUTA_ENV.get(clave)
    valor_env = _env_texto(env_name) if env_name else None
    valor = valor_env if valor_env is not None else str(rutas.get(clave, default)).strip()
    path = Path(valor or default).expanduser()
    if not path.is_absolute():
        path = PROJECT_DIR / path
    return path


def carpeta_compartida() -> Path:
    # El default es local/portable. Producción puede sobreescribirlo mediante
    # SATYS_SHARED_DIR o config/configuracion_local.json.
    return ruta_configurada("carpeta_compartida", "shared")
