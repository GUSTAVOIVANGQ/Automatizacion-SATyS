#!/usr/bin/env python3
"""Regla única para decidir si un Registro SATyS necesita reintento."""

from __future__ import annotations

import json
import os
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


def _ruta_extendida_windows(ruta: Path) -> str:
    """Devuelve una ruta absoluta recorrible aunque supere MAX_PATH."""
    absoluta = os.path.abspath(os.fspath(ruta))
    if os.name != "nt" or absoluta.startswith("\\\\?\\"):
        return absoluta
    if absoluta.startswith("\\\\"):
        return "\\\\?\\UNC\\" + absoluta[2:]
    return "\\\\?\\" + absoluta


def _iter_archivos_recursivos_robusto(carpeta: Path) -> Iterator[tuple[Path, Path]]:
    """Produce (ruta normal, ruta extendida) sin depender de ``Path.rglob``."""
    carpeta = Path(carpeta)
    if not carpeta.exists() or not carpeta.is_dir():
        return
    raiz_normal = Path(os.path.abspath(os.fspath(carpeta)))
    raiz_recorrido = _ruta_extendida_windows(raiz_normal)
    for raiz, _, archivos in os.walk(raiz_recorrido):
        for nombre in archivos:
            path_extendido = Path(os.path.join(raiz, nombre))
            relativa = Path(os.path.relpath(os.fspath(path_extendido), raiz_recorrido))
            yield raiz_normal / relativa, path_extendido


def iter_archivos_descargados(carpeta: Path) -> Iterator[Path]:
    """Recorre archivos válidos dentro de la carpeta del Registro, incluso anidados."""
    for path_normal, path_extendido in _iter_archivos_recursivos_robusto(carpeta):
        if es_archivo_descargado_valido(path_extendido):
            yield path_normal


def carpeta_tiene_descarga_real(carpeta: Path) -> bool:
    """Indica si existe al menos un archivo real; no decide completitud por sí sola."""
    return next(iter_archivos_descargados(carpeta), None) is not None


def es_archivo_publicable_output(path: Path) -> bool:
    """True sólo para un archivo real que puede publicarse bajo ``output/``.

    Los JSON son evidencia interna del pipeline y permanecen exclusivamente en
    ``descargas/``. Se excluye cualquier ``.json`` (sin distinguir mayúsculas)
    para que un nombre nuevo de metadata no pueda filtrarse accidentalmente a
    la salida documental.
    """
    return es_archivo_descargado_valido(Path(path)) and Path(path).suffix.lower() != ".json"


def iter_archivos_publicables_output(carpeta: Path) -> Iterator[Path]:
    """Recorre únicamente archivos reales autorizados para ``output/``."""
    for path_normal, path_extendido in _iter_archivos_recursivos_robusto(carpeta):
        if es_archivo_publicable_output(path_extendido):
            yield path_normal


def depurar_json_output(output_base: Path) -> list[Path]:
    """Elimina JSON heredados dentro de una raíz ``output`` explícita.

    La operación está deliberadamente acotada a archivos ``*.json`` y nunca
    toca ``descargas/``. Devuelve las rutas retiradas para auditoría/logs.
    """
    output_base = Path(output_base)
    if not output_base.exists() or not output_base.is_dir():
        return []

    raiz_recorrido = _ruta_extendida_windows(output_base)

    eliminados: list[Path] = []
    for raiz, _, archivos in os.walk(raiz_recorrido):
        for nombre in archivos:
            if Path(nombre).suffix.lower() != ".json":
                continue
            path_extendido = os.path.join(raiz, nombre)
            try:
                os.unlink(path_extendido)
                eliminados.append(Path(path_extendido))
            except OSError:
                # Conservar la lista parcial evita afirmar que un archivo fue
                # eliminado cuando Windows o el almacenamiento lo rechazaron.
                continue
    return eliminados


def _leer_metadata_completo(carpeta: Path) -> tuple[dict, str]:
    """Lee metadata de auditoría y devuelve (objeto, motivo_error)."""
    metadata_path = Path(carpeta) / "metadata_completo.json"
    metadata_extendida = _ruta_extendida_windows(metadata_path)
    if not os.path.isfile(metadata_extendida):
        return {}, "metadata_completo_ausente"
    try:
        with open(metadata_extendida, "r", encoding="utf-8-sig") as stream:
            metadata = json.load(stream)
    except (OSError, ValueError, TypeError):
        return {}, "metadata_completo_ilegible_o_corrupto"
    if not isinstance(metadata, dict):
        return {}, "metadata_completo_no_es_objeto"
    return metadata, ""


def auditar_carpeta_descarga(carpeta: Path) -> dict:
    """Audita una descarga de cualquier bandeja sin modificarla.

    Una carpeta sólo es completa cuando metadata, conteos y evidencia física
    coinciden. La respuesta incluye motivos estables para logs y reportes.
    Esta función jamás elimina, renombra ni mueve archivos de ``descargas/``.
    """
    carpeta = Path(carpeta)
    motivos: list[str] = []

    def agregar(motivo: str) -> None:
        if motivo and motivo not in motivos:
            motivos.append(motivo)

    if not carpeta.exists():
        return {"completo": False, "motivos": ["carpeta_inexistente"], "metadata": {}}
    if not carpeta.is_dir():
        return {"completo": False, "motivos": ["ruta_no_es_carpeta"], "metadata": {}}

    inventario = list(_iter_archivos_recursivos_robusto(carpeta))
    if not inventario:
        agregar("carpeta_vacia")

    for path_normal, path_extendido in inventario:
        nombre = path_normal.name
        suffix = path_normal.suffix.lower()
        if suffix in EXTENSIONES_TEMPORALES:
            agregar("archivo_temporal_pendiente")
            continue
        if nombre in METADATA_GENERADA or nombre in ARCHIVOS_AUXILIARES:
            continue
        try:
            if path_extendido.stat().st_size <= 0:
                agregar("archivo_real_vacio")
        except OSError:
            agregar("archivo_real_ilegible")

    archivos_reales = list(iter_archivos_descargados(carpeta))
    if not archivos_reales:
        agregar("sin_archivos_reales")
    if any(path.suffix.lower() == ".zip" for path in archivos_reales):
        agregar("zip_pendiente_de_extraer")

    metadata, error_metadata = _leer_metadata_completo(carpeta)
    agregar(error_metadata)
    if not metadata:
        return {"completo": False, "motivos": motivos, "metadata": {}}

    if str(metadata.get("estado") or "").strip().upper() != "OK":
        agregar("estado_metadata_no_ok")
    if "coincide" in metadata and metadata.get("coincide") is not True:
        agregar("metadata_no_coincide")
    if (
        "documentos_portal_completos" in metadata
        and metadata.get("documentos_portal_completos") is not True
    ):
        agregar("recorrido_documentos_portal_incompleto")

    archivos = metadata.get("archivos")
    if not isinstance(archivos, list) or not archivos:
        agregar("lista_archivos_ausente_o_vacia")
        archivos = []
    if any(not isinstance(item, dict) for item in archivos):
        agregar("lista_archivos_invalida")
    archivos_dict = [item for item in archivos if isinstance(item, dict)]
    archivos_ok = [item for item in archivos_dict if item.get("ok") is True]
    if not archivos_ok:
        agregar("ningun_archivo_reportado_ok")
    if any(item.get("ok") is not True for item in archivos_dict):
        agregar("uno_o_mas_archivos_reportados_con_error")

    def entero_metadata(*claves: str) -> int | None:
        for clave in claves:
            if clave not in metadata:
                continue
            try:
                return int(metadata.get(clave))
            except (TypeError, ValueError):
                return None
        return None

    total = entero_metadata("total_archivos_encontrados", "total_archivos")
    total_ok = entero_metadata("total_archivos_ok")
    total_error = entero_metadata("total_archivos_error")
    if total is None:
        agregar("conteo_total_ausente_o_invalido")
    elif total <= 0 or total != len(archivos):
        agregar("conteo_total_no_coincide")
    if total_ok is None:
        agregar("conteo_ok_ausente_o_invalido")
    elif total_ok != len(archivos_ok):
        agregar("conteo_ok_no_coincide")
    if total_error is not None and total_error != len(archivos) - len(archivos_ok):
        agregar("conteo_error_no_coincide")
    if total_error is not None and total_error != 0:
        agregar("conteo_reporta_errores")
    if total is not None and total_ok is not None and total_ok != total:
        agregar("no_todos_los_archivos_quedaron_ok")

    nombres_reales = {path.name.casefold() for path in archivos_reales}
    for item in archivos_ok:
        nombre = Path(
            str(item.get("archivo") or item.get("nombre_original") or "")
        ).name.strip()
        if not nombre:
            agregar("archivo_ok_sin_nombre")
        elif nombre.casefold() not in nombres_reales:
            agregar("archivo_reportado_ok_no_existe_fisicamente")

    return {
        "completo": not motivos,
        "motivos": motivos,
        "metadata": metadata,
        "total_archivos_reales": len(archivos_reales),
    }


def carpeta_descarga_esta_completa(carpeta: Path) -> bool:
    """Regla única de completitud para cualquier expediente SATyS."""
    return bool(auditar_carpeta_descarga(carpeta)["completo"])


def registro_esta_completo(descargas_base: Path, registro: str) -> bool:
    """Audita estrictamente ``descargas/<REGISTRO>/`` sin borrar su contenido."""
    return carpeta_descarga_esta_completa(Path(descargas_base) / registro)


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
    """Aplica auditoría común y además valida la identidad bandeja/Folio."""
    auditoria = auditar_carpeta_descarga(carpeta)
    if not auditoria["completo"]:
        return False
    metadata = auditoria["metadata"]

    bandeja_meta, folio_meta = _metadata_objetivo_internos(metadata)
    if _normalizar_bandeja_internos(bandeja_meta) != _normalizar_bandeja_internos(bandeja):
        return False
    if str(folio_meta).strip() != str(folio).strip():
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
