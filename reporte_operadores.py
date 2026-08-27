#!/usr/bin/env python3
"""Reportes CSV auditables para la resolución y organización de operadores."""

from __future__ import annotations

import csv
import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

from buscar_concesionario import normalizar_nombre


CAMPOS_REPORTE = [
    "fecha_proceso",
    "modo",
    "estado",
    "folio",
    "folio_id",
    "registro",
    "folio_opc",
    "es_correo",
    "identificador_correo",
    "id_solicitante",
    "nombre_operador_satys",
    "fuente_nombre_operador",
    "nombre_normalizado",
    "metodo",
    "fuente",
    "id_operador_resuelto",
    "nombre_operador_rpc",
    "exactitud",
    "margen",
    "motivo",
    "consulta_rpc",
    "candidatos",
    "operadores_resueltos",
    "razones_sin_id",
    "duplicados_correo_retirados",
    "errores_organizacion_correo",
    "carpeta_descargas",
    "carpeta_output",
    "archivos_copiados",
]


def _fila_reporte(resultado: dict[str, Any], modo: str, fecha: str) -> dict[str, Any]:
    rpc = resultado.get("rpc_resultado") or {}
    nombre_satys = str(
        rpc.get("nombre_operador_satys")
        or resultado.get("concesionario")
        or resultado.get("nombre_operador")
        or ""
    ).strip()

    if resultado.get("es_correo"):
        estado = "correo" if resultado.get("organizado_ok") else "correo_error"
    elif resultado.get("rpc_ok"):
        estado = "organizado" if resultado.get("organizado_ok", True) else "resuelto_no_organizado"
    elif nombre_satys:
        estado = "sin_operador"
    else:
        estado = "metadata_incompleta"

    pendientes = resultado.get("archivos_pendientes")
    if isinstance(pendientes, list):
        archivos_copiados = len(pendientes)
    else:
        archivos_copiados = resultado.get("archivos_copiados", "")

    candidatos = rpc.get("candidatos") or []
    operadores = rpc.get("operadores") or []
    ids_operador = (
        " | ".join(
            str(item.get("idBp") or item.get("numero_rpc") or "SIN_ID")
            for item in operadores
        )
        if operadores
        else (rpc.get("idBp") or rpc.get("numero_rpc", ""))
    )
    return {
        "fecha_proceso": fecha,
        "modo": modo,
        "estado": estado,
        "folio": resultado.get("folio", ""),
        "folio_id": resultado.get("folio_id", ""),
        "registro": resultado.get("registro", ""),
        "folio_opc": resultado.get("folio_opc", ""),
        "es_correo": bool(resultado.get("es_correo")),
        "identificador_correo": resultado.get("identificador_correo", ""),
        "id_solicitante": resultado.get("id_solicitante") or rpc.get("id_solicitante", ""),
        "nombre_operador_satys": nombre_satys,
        "fuente_nombre_operador": resultado.get("fuente_nombre_operador", ""),
        "nombre_normalizado": rpc.get("nombre_normalizado") or normalizar_nombre(nombre_satys).replace(" ", ""),
        "metodo": rpc.get("metodo", ""),
        "fuente": rpc.get("fuente", ""),
        "id_operador_resuelto": ids_operador,
        "nombre_operador_rpc": rpc.get("nombre_completo", ""),
        "exactitud": rpc.get("score", 0.0),
        "margen": rpc.get("margen", ""),
        "motivo": rpc.get("motivo", ""),
        "consulta_rpc": rpc.get("consulta_rpc", ""),
        "candidatos": json.dumps(candidatos, ensure_ascii=False, separators=(",", ":")),
        "operadores_resueltos": json.dumps(
            operadores,
            ensure_ascii=False,
            separators=(",", ":"),
        ),
        "razones_sin_id": " | ".join(rpc.get("razones_sin_id") or []),
        "duplicados_correo_retirados": " | ".join(
            resultado.get("duplicados_correo_retirados") or []
        ),
        "errores_organizacion_correo": " | ".join(
            resultado.get("errores_organizacion_correo") or []
        ),
        "carpeta_descargas": resultado.get("descargas_dir", ""),
        "carpeta_output": resultado.get("output_dir") or resultado.get("sin_operador_dir", ""),
        "archivos_copiados": archivos_copiados,
    }


def _escribir_csv_atomico(path: Path, filas: Iterable[dict[str, Any]]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporal = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        with temporal.open("w", encoding="utf-8-sig", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=CAMPOS_REPORTE, extrasaction="ignore")
            writer.writeheader()
            writer.writerows(filas)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporal, path)
    finally:
        temporal.unlink(missing_ok=True)
    return path


def generar_reportes_operadores(
    resultados: list[dict[str, Any]],
    *,
    modo: str,
    logs_dir: str | Path = "logs",
    fecha: datetime | None = None,
) -> dict[str, Any]:
    """Genera auditoría completa y reporte específico de ``sin_operador``."""
    ahora = fecha or datetime.now()
    fecha_iso = ahora.isoformat(timespec="seconds")
    marca = ahora.strftime("%Y%m%d_%H%M%S")
    modo_seguro = "".join(c if c.isalnum() or c in "_-" else "_" for c in modo.strip().lower()) or "proceso"
    base = Path(logs_dir)

    filas = [_fila_reporte(resultado, modo_seguro, fecha_iso) for resultado in resultados]
    sin_operador = [
        fila for fila in filas
        if fila["estado"] not in {"organizado", "correo"}
    ]

    auditoria_path = _escribir_csv_atomico(
        base / f"auditoria_operadores_{modo_seguro}_{marca}.csv",
        filas,
    )
    sin_operador_path = _escribir_csv_atomico(
        base / f"sin_operador_{modo_seguro}_{marca}.csv",
        sin_operador,
    )
    ultimo_path = _escribir_csv_atomico(
        base / f"sin_operador_{modo_seguro}_ultimo.csv",
        sin_operador,
    )
    return {
        "auditoria_csv": str(auditoria_path),
        "sin_operador_csv": str(sin_operador_path),
        "sin_operador_ultimo_csv": str(ultimo_path),
        "total": len(filas),
        "pendientes": len(sin_operador),
    }
