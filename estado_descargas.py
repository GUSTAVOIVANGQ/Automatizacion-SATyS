#!/usr/bin/env python3
"""Regla única para decidir si un Registro SATyS necesita reintento."""

from __future__ import annotations

import json
import re
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


def slug_bandeja_internos(texto: str) -> str:
    """Replica el nombre estable usado por Parte 1 para cada bandeja de Internos."""
    valor = (texto or "sin_bandeja").strip().lower()
    for origen, destino in {
        " ": "_",
        "/": "_",
        "\\": "_",
        "+": "plus",
    }.items():
        valor = valor.replace(origen, destino)
    valor = re.sub(r"[^a-z0-9_.-]+", "_", valor)
    valor = re.sub(r"_+", "_", valor).strip("._-")
    return valor or "sin_bandeja"


def _normalizar_bandeja_internos(texto: object) -> str:
    valor = str(texto or "").strip().lower()
    valor = valor.translate(str.maketrans("áéíóúüñ", "aeiouun"))
    return re.sub(r"[^a-z0-9]+", "", valor)


def _metadata_objetivo_internos(metadata: dict) -> tuple[str, str]:
    """Obtiene (bandeja, folio de tabla) de cualquiera de las capas de metadata."""
    capas = [
        metadata,
        metadata.get("metadatos_satys") or {},
        metadata.get("metadatos_tramite") or {},
        metadata.get("meta_satys") or {},
    ]
    bandeja = ""
    folio = ""
    for capa in capas:
        if not isinstance(capa, dict):
            continue
        bandeja = bandeja or str(capa.get("bandeja_internos") or "").strip()
        folio = folio or str(capa.get("folio_tabla_internos") or "").strip()
    if not folio:
        for capa in capas:
            if isinstance(capa, dict):
                folio = str(capa.get("folio") or "").strip()
                if folio:
                    break
    return bandeja, folio


def _carpeta_internos_completa(carpeta: Path, bandeja: str, folio: str) -> bool:
    """Valida metadata, conteo y presencia física de cada archivo reportado como OK."""
    metadata_path = carpeta / "metadata_completo.json"
    try:
        metadata = json.loads(metadata_path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError, TypeError):
        return False
    if not isinstance(metadata, dict):
        return False

    bandeja_meta, folio_meta = _metadata_objetivo_internos(metadata)
    if _normalizar_bandeja_internos(bandeja_meta) != _normalizar_bandeja_internos(bandeja):
        return False
    if str(folio_meta).strip() != str(folio).strip():
        return False

    archivos = metadata.get("archivos")
    if not isinstance(archivos, list) or not archivos:
        return False
    archivos_ok = [item for item in archivos if isinstance(item, dict) and item.get("ok") is True]
    if not archivos_ok:
        return False
    try:
        total = int(metadata.get("total_archivos_encontrados", len(archivos)))
        total_ok = int(metadata.get("total_archivos_ok", len(archivos_ok)))
        total_error = int(metadata.get("total_archivos_error", total - total_ok))
    except (TypeError, ValueError):
        return False
    if (
        metadata.get("estado") != "OK"
        or metadata.get("coincide") is not True
        or total <= 0
        or total != len(archivos)
        or total_ok != len(archivos_ok)
        or total_error != 0
        or total_ok != total
    ):
        return False

    archivos_reales = list(iter_archivos_descargados(carpeta))
    if not archivos_reales:
        return False
    # Todo ZIP descargado por este flujo debe haberse descomprimido y eliminado.
    # Si queda uno, la extracción no terminó y el objetivo debe reintentarse.
    if any(path.suffix.lower() == ".zip" for path in archivos_reales):
        return False

    nombres_reales = {path.name.casefold() for path in archivos_reales}
    for item in archivos_ok:
        nombre = Path(str(item.get("archivo") or "")).name.strip()
        if not nombre or nombre.casefold() not in nombres_reales:
            return False
    return True


def objetivo_internos_esta_completo(
    descargas_base: Path,
    bandeja: str,
    folio: str,
) -> bool:
    """True sólo si la pareja (bandeja, folio) conserva todos sus archivos auditados."""
    folio_limpio = str(folio or "").strip()
    base = Path(descargas_base) / "internos" / slug_bandeja_internos(bandeja)
    if not base.is_dir() or not folio_limpio:
        return False

    candidatos = [base / folio_limpio]
    try:
        candidatos.extend(sorted(base.glob(f"{folio_limpio}_*")))
    except OSError:
        pass
    vistos: set[str] = set()
    for carpeta in candidatos:
        clave = str(carpeta).casefold()
        if clave in vistos or not carpeta.is_dir():
            continue
        vistos.add(clave)
        if _carpeta_internos_completa(carpeta, bandeja, folio_limpio):
            return True
    return False
