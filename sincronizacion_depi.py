#!/usr/bin/env python3
"""Sincronización no destructiva de salidas hacia CRT Recurso DEPI."""

from __future__ import annotations

import shutil
from dataclasses import dataclass, field
from pathlib import Path

# Únicas salidas autorizadas para CRT Recurso DEPI. Se copian mediante merge:
# se sobrescriben coincidencias y jamás se eliminan archivos adicionales del destino.
DIRECTORIOS_OPERATIVOS = (
    "output",
    "descargas",
)

ARCHIVOS_OPERATIVOS = (
    "TrámitesCRT.xlsx",
)

# Nunca se sincronizan credenciales, sesiones autenticadas, cachés o depuración.
NOMBRES_EXCLUIDOS = frozenset({
    "configuracion_local.json",
    "sesion_guardada.json",
})


@dataclass
class ResultadoSincronizacion:
    archivos_copiados: int = 0
    directorios_creados: int = 0
    omitidos: int = 0
    errores: list[str] = field(default_factory=list)


def _misma_ruta(origen: Path, destino: Path) -> bool:
    try:
        return origen.resolve() == destino.resolve()
    except OSError:
        return False


def copiar_archivo_sobrescribiendo(origen: Path, destino: Path, resultado: ResultadoSincronizacion) -> None:
    if origen.name in NOMBRES_EXCLUIDOS or _misma_ruta(origen, destino):
        resultado.omitidos += 1
        return
    try:
        destino.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(origen, destino)
        resultado.archivos_copiados += 1
    except Exception as exc:
        resultado.errores.append(f"{origen} -> {destino}: {exc}")


def copiar_directorio_merge(origen: Path, destino: Path, resultado: ResultadoSincronizacion) -> None:
    """Copia recursivamente sin borrar nada del destino."""
    if not origen.exists() or not origen.is_dir() or _misma_ruta(origen, destino):
        resultado.omitidos += 1
        return
    try:
        destino.mkdir(parents=True, exist_ok=True)
        resultado.directorios_creados += 1
    except Exception as exc:
        resultado.errores.append(f"No se pudo crear {destino}: {exc}")
        return

    for item in origen.rglob("*"):
        relativo = item.relative_to(origen)
        destino_item = destino / relativo
        if item.name in NOMBRES_EXCLUIDOS:
            resultado.omitidos += 1
            continue
        if item.is_dir():
            try:
                destino_item.mkdir(parents=True, exist_ok=True)
                resultado.directorios_creados += 1
            except Exception as exc:
                resultado.errores.append(f"No se pudo crear {destino_item}: {exc}")
        elif item.is_file():
            copiar_archivo_sobrescribiendo(item, destino_item, resultado)


def sincronizar_salidas(
    project_dir: Path,
    destino_raiz: Path,
    *,
    directorios: tuple[str, ...] = DIRECTORIOS_OPERATIVOS,
    archivos: tuple[str, ...] = ARCHIVOS_OPERATIVOS,
) -> ResultadoSincronizacion:
    """Sincroniza únicamente Excel, output/ y descargas/; sobrescribe y no borra."""
    project_dir = Path(project_dir)
    destino_raiz = Path(destino_raiz)
    resultado = ResultadoSincronizacion()

    try:
        destino_raiz.mkdir(parents=True, exist_ok=True)
    except Exception as exc:
        resultado.errores.append(f"No se pudo acceder a {destino_raiz}: {exc}")
        return resultado

    for nombre in directorios:
        copiar_directorio_merge(project_dir / nombre, destino_raiz / nombre, resultado)

    for nombre in archivos:
        origen = project_dir / nombre
        if origen.exists() and origen.is_file():
            copiar_archivo_sobrescribiendo(origen, destino_raiz / nombre, resultado)
        else:
            resultado.omitidos += 1

    return resultado
