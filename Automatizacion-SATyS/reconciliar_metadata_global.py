#!/usr/bin/env python3
"""Reconstruye TrámitesCRT.xlsx desde todos los metadatos locales.

Este proceso es deliberadamente independiente de que SATyS tenga registros
nuevos. Usa ``metadata_satys.json``/``metadata_tramite_nuevo.json`` como fuente
de verdad, cruza ``id_solicitante`` contra el Excel RPC y sobrescribe los campos
automáticos —incluida ``Ruta``— conservando columnas manuales del maestro.
"""

from __future__ import annotations

import argparse
import json
import logging
import re
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any

import buscar_concesionario as bc
from Parte3_rpc import construir_ruta
from configuracion_local import ruta_configurada
from generar_excel_metadata_json import generar_excel_metadata_json
from reconciliar_tramites_desde_folios import reconciliar
from rutas_salida import destino_sin_operador, es_folio_opc_correo, folio_opc_desde_metadata

log = logging.getLogger("SATyS-ReconciliacionGlobal")
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%H:%M:%S",
)

REGISTRO_RE = re.compile(r"\b[A-Z]{2,6}\d{2}-\d{3,}\b", re.IGNORECASE)
JSON_NAMES = ("metadata_satys.json", "metadata_tramite_nuevo.json")


def _es_metadata_internos(meta_satys: dict[str, Any], meta_tn: dict[str, Any]) -> bool:
    """Evita mezclar expedientes de Internos con la hoja Turnados recibidos."""
    for metadata in (meta_satys, meta_tn):
        if str(metadata.get("satys_flujo") or "").strip().lower() == "internos":
            return True
        if metadata.get("bandeja_internos") or metadata.get("folio_tabla_internos"):
            return True
    return False


def _read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8-sig", errors="replace"))
        return value if isinstance(value, dict) else {}
    except Exception as exc:
        log.warning("No se pudo leer %s: %s", path, exc)
        return {}


def _registro(meta_satys: dict[str, Any], meta_tn: dict[str, Any], fallback: str) -> str:
    for key in ("registro", "numero_registro", "1711"):
        value = meta_satys.get(key) or meta_tn.get(key)
        text = str(value or "").strip().upper().replace(" ", "")
        match = REGISTRO_RE.search(text)
        if match:
            return match.group(0).upper()
    match = REGISTRO_RE.search(str(fallback or "").strip().upper())
    return match.group(0).upper() if match else ""


def _folio(meta_satys: dict[str, Any], meta_tn: dict[str, Any], fallback: str) -> str:
    directo = meta_satys.get("folio") or meta_tn.get("folio")
    if directo not in (None, ""):
        return str(directo).strip()
    folio_opc = folio_opc_desde_metadata(meta_satys, meta_tn)
    if folio_opc:
        numeros = re.sub(r"[^0-9]", "", folio_opc)
        if numeros:
            return numeros
    memo = meta_satys.get("memo_folio_opc") or meta_tn.get("memo_folio_opc")
    return str(memo or fallback or "").strip()


def _identificador_salida(carpeta: Path, descargas_base: Path, registro: str) -> str:
    """Replica la convención de main_procesar para carpetas normales/heredadas."""
    try:
        rel = carpeta.relative_to(descargas_base)
    except ValueError:
        return registro or carpeta.name
    if len(rel.parts) == 1:
        return rel.parts[0]
    return f"{rel.parts[0]}__{rel.parts[-1]}"


def _metadata_score(carpeta: Path, meta_satys: dict[str, Any], meta_tn: dict[str, Any]) -> tuple[int, float]:
    campos = sum(1 for value in list(meta_satys.values()) + list(meta_tn.values()) if value not in (None, ""))
    mtimes = [p.stat().st_mtime for p in (carpeta / JSON_NAMES[0], carpeta / JSON_NAMES[1]) if p.exists()]
    return campos, max(mtimes, default=0.0)


def descubrir_metadata(descargas_base: Path) -> tuple[list[dict[str, Any]], list[str]]:
    """Devuelve una carpeta canónica por Registro y reporta duplicados."""
    por_registro: dict[str, dict[str, Any]] = {}
    duplicados: list[str] = []
    if not descargas_base.exists():
        return [], []

    carpetas = sorted(
        {path.parent for name in JSON_NAMES for path in descargas_base.rglob(name)},
        key=lambda p: str(p).upper(),
    )
    for carpeta in carpetas:
        meta_satys = _read_json(carpeta / JSON_NAMES[0])
        meta_tn = _read_json(carpeta / JSON_NAMES[1])
        if not meta_satys and not meta_tn:
            continue
        if _es_metadata_internos(meta_satys, meta_tn):
            continue
        registro = _registro(meta_satys, meta_tn, carpeta.name)
        if not registro:
            log.warning("Se omite metadata sin Registro CRT válido: %s", carpeta)
            continue
        item = {
            "carpeta": carpeta,
            "meta_satys": meta_satys,
            "meta_tn": meta_tn,
            "registro": registro,
            "score": _metadata_score(carpeta, meta_satys, meta_tn),
        }
        anterior = por_registro.get(registro)
        if anterior is None or item["score"] > anterior["score"]:
            if anterior is not None:
                duplicados.append(registro)
            por_registro[registro] = item
        else:
            duplicados.append(registro)

    return [por_registro[k] for k in sorted(por_registro)], sorted(set(duplicados))


def catalogo_rpc_mas_reciente(base_rpc: Path) -> Path:
    archivos = sorted(
        base_rpc.glob("03_concesiones_permisos_autorizaciones_*.xlsx"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    if not archivos:
        raise FileNotFoundError(
            f"No existe Excel RPC en {base_rpc}. No se sobrescribirá TrámitesCRT.xlsx con rutas indeterminadas."
        )
    return archivos[0]


def cargar_indice_rpc(base_rpc: Path) -> tuple[dict[str, dict[str, Any]], Path]:
    excel_rpc = catalogo_rpc_mas_reciente(base_rpc)
    catalogo = bc.cargar_catalogo_desde_excel(excel_rpc, "copeau", solo_vigentes=False)
    preparado = bc.preparar_catalogo_para_matching(catalogo)
    indice = {bc.normalizar_id(item.get("idBp")): item for item in preparado if bc.normalizar_id(item.get("idBp"))}
    if not indice:
        raise RuntimeError(f"El Excel RPC {excel_rpc} no produjo un catálogo válido.")
    return indice, excel_rpc


def _copiar_correos_existentes(carpeta: Path, destino: Path) -> int:
    """Migra no destructivamente los CORREO-2408 al nuevo directorio."""
    copiados = 0
    destino.mkdir(parents=True, exist_ok=True)
    for item in carpeta.rglob("*"):
        if not item.is_file() or item.suffix.lower() == ".json":
            continue
        relativo = item.relative_to(carpeta)
        target = destino / relativo
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(item, target)
        copiados += 1
    return copiados


def construir_resultados(
    descargas_base: Path,
    output_base: Path,
    indice_rpc: dict[str, dict[str, Any]],
    *,
    migrar_correos: bool = True,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    items, duplicados = descubrir_metadata(descargas_base)
    resultados: list[dict[str, Any]] = []
    stats = {
        "metadata": len(items),
        "rpc_ok": 0,
        "sin_operador": 0,
        "sin_operador_correo": 0,
        "archivos_correo_copiados": 0,
        "metadata_duplicada": duplicados,
    }

    for item in items:
        carpeta: Path = item["carpeta"]
        meta_satys = item["meta_satys"]
        meta_tn = item["meta_tn"]
        registro = item["registro"]
        folio = _folio(meta_satys, meta_tn, registro)
        folio_opc = folio_opc_desde_metadata(meta_satys, meta_tn)
        identificador = _identificador_salida(carpeta, descargas_base, registro)
        id_solicitante = bc.normalizar_id(
            meta_satys.get("id_solicitante") or meta_tn.get("id_solicitante")
        )
        match = indice_rpc.get(id_solicitante) if id_solicitante else None

        resultado: dict[str, Any] = {
            "folio": folio,
            "folio_id": identificador,
            "folio_opc": folio_opc,
            "registro": registro,
            "descargas_dir": str(carpeta),
            "id_solicitante": id_solicitante,
            "nombre_operador": (
                meta_satys.get("nombre_operador")
                or meta_tn.get("nombre_operador")
                or ""
            ),
            "representante_legal": (
                meta_satys.get("representante_legal")
                or meta_tn.get("representante_legal")
                or ""
            ),
        }

        if match:
            ruta = construir_ruta(match["nombre_completo"], match["idBp"])
            destino = output_base / ruta.replace("\\", "/")
            resultado.update({
                "rpc_ok": True,
                "nombre_operador": match["nombre_completo"],
                "rpc_resultado": {
                    "ok": True,
                    "score": 1.0,
                    "metodo": "id_exacto_reconciliacion_global",
                    "idBp": match["idBp"],
                    "numero_rpc": match["idBp"],
                    "nombre_completo": match["nombre_completo"],
                    "ruta": ruta,
                },
                "output_dir": str(destino),
            })
            stats["rpc_ok"] += 1
        else:
            destino = destino_sin_operador(output_base, identificador, folio_opc)
            resultado.update({
                "rpc_ok": False,
                "rpc_resultado": {
                    "ok": False,
                    "score": 0.0,
                    "metodo": "id_exacto_reconciliacion_global",
                    "id_solicitante": id_solicitante,
                    "motivo": "id_solicitante_no_encontrado" if id_solicitante else "metadata_sin_id_solicitante",
                },
                "sin_operador_dir": str(destino),
                "output_dir": str(destino),
            })
            stats["sin_operador"] += 1
            if es_folio_opc_correo(folio_opc):
                stats["sin_operador_correo"] += 1
                if migrar_correos:
                    stats["archivos_correo_copiados"] += _copiar_correos_existentes(carpeta, destino)

        resultados.append(resultado)

    return resultados, stats


def ejecutar(
    *,
    descargas_base: Path,
    output_base: Path,
    excel_path: Path,
    base_rpc: Path,
    project_root: Path,
    migrar_correos: bool = True,
    crear_backup: bool = True,
) -> dict[str, Any]:
    indice_rpc, excel_rpc = cargar_indice_rpc(base_rpc)
    resultados, stats = construir_resultados(
        descargas_base,
        output_base,
        indice_rpc,
        migrar_correos=migrar_correos,
    )
    if not resultados:
        raise RuntimeError(f"No se encontraron metadatos válidos bajo {descargas_base}.")

    consolidado = generar_excel_metadata_json(
        resultados=resultados,
        descargas_base=descargas_base,
        output_base=output_base,
        excel_salida=output_base / "Folios_Datos_Completos.xlsx",
        project_root=project_root,
    )
    reconciliacion = reconciliar(excel_path, consolidado, crear_backup=crear_backup)
    if reconciliacion.get("routes_blank"):
        raise RuntimeError(
            f"La reconciliación dejó {reconciliacion['routes_blank']} Ruta(s) vacía(s); no se acepta como completa."
        )

    return {
        "ok": True,
        "fecha": datetime.now().isoformat(),
        "excel_rpc": str(excel_rpc),
        "excel_consolidado": str(consolidado),
        "excel_maestro": str(excel_path),
        "estadisticas": stats,
        "reconciliacion": reconciliacion,
    }


def construir_parser() -> argparse.ArgumentParser:
    project_root = Path(__file__).resolve().parent
    parser = argparse.ArgumentParser(
        description="Sobrescribe campos automáticos de TrámitesCRT.xlsx desde todos los metadata JSON."
    )
    parser.add_argument("--descargas", type=Path, default=ruta_configurada("descargas", "descargas"))
    parser.add_argument("--output", type=Path, default=ruta_configurada("output", "output"))
    parser.add_argument("--excel", type=Path, default=ruta_configurada("excel", "TrámitesCRT.xlsx"))
    parser.add_argument("--base-rpc", type=Path, default=project_root / "base_de_datos_rpc")
    parser.add_argument("--resumen-json", type=Path, default=project_root / "logs" / "reconciliacion_global_ultimo.json")
    parser.add_argument("--sin-migrar-correos", action="store_true")
    parser.add_argument("--sin-backup", action="store_true")
    return parser


def main() -> int:
    args = construir_parser().parse_args()
    args.resumen_json.parent.mkdir(parents=True, exist_ok=True)
    try:
        resumen = ejecutar(
            descargas_base=args.descargas,
            output_base=args.output,
            excel_path=args.excel,
            base_rpc=args.base_rpc,
            project_root=Path(__file__).resolve().parent,
            migrar_correos=not args.sin_migrar_correos,
            crear_backup=not args.sin_backup,
        )
        args.resumen_json.write_text(json.dumps(resumen, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps(resumen, ensure_ascii=False, indent=2))
        return 0
    except Exception as exc:
        resumen = {"ok": False, "fecha": datetime.now().isoformat(), "error": str(exc)}
        args.resumen_json.write_text(json.dumps(resumen, ensure_ascii=False, indent=2), encoding="utf-8")
        log.exception("Reconciliación global fallida: %s", exc)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
