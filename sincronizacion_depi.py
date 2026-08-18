#!/usr/bin/env python3
"""Sincronización no destructiva de salidas hacia CRT Recurso DEPI."""

from __future__ import annotations

import os
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


def _montaje_real_para(path: Path) -> Path | None:
    """Devuelve el ancestro montado más cercano, incluso si ``path`` no existe."""
    actual = Path(path)
    while not actual.exists() and actual != actual.parent:
        actual = actual.parent
    for candidato in (actual, *actual.parents):
        try:
            if os.path.ismount(candidato):
                return candidato
        except OSError:
            continue
    return None


def validar_destino_compartido(destino_raiz: Path) -> str | None:
    """Evita escribir en el disco local cuando el CIFS bajo /depi no está montado."""
    destino_raiz = Path(destino_raiz)
    exigir = os.getenv("SATYS_REQUIRE_SHARED_MOUNT", "1").strip() != "0"
    if not exigir:
        return None
    texto_normalizado = str(destino_raiz).replace("\\", "/")
    bajo_depi_lexico = texto_normalizado == "/depi" or texto_normalizado.startswith("/depi/")
    if os.name != "posix" and bajo_depi_lexico:
        return (
            f"La ruta Linux {texto_normalizado} no puede validarse como montaje CIFS en {os.name}; "
            "se cancela la sincronización para no escribir en un directorio local equivalente."
        )
    if not destino_raiz.is_absolute():
        return None
    try:
        bajo_depi = destino_raiz == Path("/depi") or Path("/depi") in destino_raiz.parents
    except Exception:
        bajo_depi = bajo_depi_lexico
    if not bajo_depi:
        return None
    montaje = _montaje_real_para(destino_raiz)
    if montaje is None or montaje == Path("/"):
        return (
            f"El recurso compartido para {destino_raiz} no está montado; "
            "se cancela la sincronización para no escribir en /depi local."
        )
    return None


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

    error_montaje = validar_destino_compartido(destino_raiz)
    if error_montaje:
        resultado.errores.append(error_montaje)
        return resultado

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
