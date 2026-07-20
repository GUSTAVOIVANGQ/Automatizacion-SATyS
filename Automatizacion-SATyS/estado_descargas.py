#!/usr/bin/env python3
"""Regla única para decidir si un Registro SATyS necesita reintento."""

from __future__ import annotations

from pathlib import Path
from typing import Iterator

METADATA_GENERADA = frozenset({
    "metadata_completo.json",
    "metadata_satys.json",
    "metadata_tramite_nuevo.json",
})

EXTENSIONES_TEMPORALES = frozenset({
    ".crdownload",
    ".part",
    ".partial",
    ".tmp",
    ".download",
})

ARCHIVOS_AUXILIARES = frozenset({
    ".DS_Store",
    "Thumbs.db",
    "desktop.ini",
})


def es_archivo_descargado_valido(path: Path) -> bool:
    """True para un archivo real del portal; metadata, temporales y vacíos no cuentan."""
    try:
        if not path.is_file():
            return False
        if path.name in METADATA_GENERADA or path.name in ARCHIVOS_AUXILIARES:
            return False
        if path.suffix.lower() in EXTENSIONES_TEMPORALES:
            return False
        return path.stat().st_size > 0
    except OSError:
        return False


def iter_archivos_descargados(carpeta: Path) -> Iterator[Path]:
    """Recorre archivos válidos dentro de la carpeta del Registro, incluso anidados."""
    if not carpeta.exists() or not carpeta.is_dir():
        return
    for path in carpeta.rglob("*"):
        if es_archivo_descargado_valido(path):
            yield path


def carpeta_tiene_descarga_real(carpeta: Path) -> bool:
    """La carpeta está completa cuando contiene al menos un archivo real válido."""
    return next(iter_archivos_descargados(carpeta), None) is not None


def registro_esta_completo(descargas_base: Path, registro: str) -> bool:
    """Aplica la única regla autorizada sobre descargas/<REGISTRO>/ únicamente."""
    return carpeta_tiene_descarga_real(Path(descargas_base) / registro)
