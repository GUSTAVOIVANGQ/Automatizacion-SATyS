#!/usr/bin/env python3
"""Reglas únicas para decidir la carpeta de salida de trámites SATyS.

La clasificación se deriva de ``metadata_satys.json`` y se reutiliza tanto al
organizar archivos como al reconstruir la columna ``Ruta`` del Excel maestro.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Mapping

SIN_OPERADOR_DIR = os.getenv("SATYS_SIN_OPERADOR_DIR", "_sin_operador").strip() or "_sin_operador"
SIN_OPERADOR_CORREO_DIR = (
    os.getenv("SATYS_SIN_OPERADOR_CORREO_DIR", "sin_operador_CORREO").strip()
    or "sin_operador_CORREO"
)


def _prefijos_correo() -> tuple[str, ...]:
    raw = os.getenv("SATYS_FOLIO_OPC_CORREO_PREFIXES", "CORREO-2408")
    prefijos = tuple(p.strip().upper() for p in raw.split(",") if p.strip())
    return prefijos or ("CORREO-2408",)


FOLIO_OPC_CORREO_PREFIXES = _prefijos_correo()


def texto_limpio(value: Any) -> str:
    return str(value or "").strip()


def folio_opc_desde_metadata(*metadatos: Mapping[str, Any] | None) -> str:
    """Obtiene el primer ``folio_opc`` no vacío de los metadatos recibidos."""
    for metadata in metadatos:
        if isinstance(metadata, Mapping):
            value = texto_limpio(metadata.get("folio_opc"))
            if value:
                return value
    return ""


def es_folio_opc_correo(folio_opc: Any) -> bool:
    value = texto_limpio(folio_opc).upper()
    return bool(value) and any(value.startswith(prefix) for prefix in FOLIO_OPC_CORREO_PREFIXES)


def carpeta_sin_operador(folio_opc: Any = "") -> str:
    """Devuelve la carpeta de revisión manual que corresponde al folio OPC."""
    return SIN_OPERADOR_CORREO_DIR if es_folio_opc_correo(folio_opc) else SIN_OPERADOR_DIR


def ruta_relativa_sin_operador(identificador: Any, folio_opc: Any = "") -> str:
    """Ruta relativa para escribir en ``TrámitesCRT.xlsx`` (separador Windows)."""
    ident = texto_limpio(identificador)
    return str(Path(carpeta_sin_operador(folio_opc)) / ident).replace("/", "\\")


def destino_sin_operador(output_base: str | Path, identificador: Any, folio_opc: Any = "") -> Path:
    """Ruta física bajo ``output/`` para un trámite sin coincidencia RPC."""
    return Path(output_base) / carpeta_sin_operador(folio_opc) / texto_limpio(identificador)
