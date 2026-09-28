#!/usr/bin/env python3
"""Reconcilia TrámitesCRT.xlsx usando Folios_Datos_Completos.xlsx como fuente.

Objetivos:
- una fila única por número de Registro CRT;
- permitir varios registros con el mismo folio/Memo;
- completar las columnas equivalentes del Excel maestro;
- escribir Ruta también para registros en ``_sin_operador``;
- eliminar únicamente filas fantasma creadas por subcarpetas de ZIP;
- conservar columnas manuales y las tres columnas administradas por SharePoint.
"""

from __future__ import annotations

import argparse
import re
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any

import openpyxl

from guardado_seguro import reemplazar_desde_temporal

REGISTRO_RE = re.compile(r"^CRT\d{2}-\d+$", re.IGNORECASE)
FORMATO_RE = re.compile(r"\bR(?:0(?:0[1-9]|1\d|2[0-7]))\b", re.IGNORECASE)
SHEET_TRAMITES = "Turnados recibidos"
SHEET_FOLIOS = "Datos_Completos"


def _texto(value: Any) -> str:
    return str(value or "").strip()


def _valor_remitente_faltante(value: Any) -> bool:
    """Vacío/SIN REMITENTE se consideran pendientes; un valor válido se preserva."""
    text = _texto(value).upper()
    text = re.sub(r"\s+", " ", text).strip()
    return text in {"", "SIN REMITENTE"}


def _registro(value: Any) -> str:
    text = _texto(value).upper()
    return text if REGISTRO_RE.fullmatch(text) else ""


def _primero(row: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        value = row.get(key)
        if value not in (None, ""):
            return value
    return ""


def _ruta_desde_output(value: Any) -> str:
    r"""Convierte una ruta absoluta o ``\output\...`` a la ruta del maestro."""
    text = _texto(value).replace("/", "\\")
    partes = [parte for parte in text.split("\\") if parte]
    indices_output = [i for i, parte in enumerate(partes) if parte.lower() == "output"]
    if indices_output:
        partes = partes[indices_output[-1] + 1:]
    return "\\".join(partes)


def _headers(ws) -> dict[str, int]:
    """Obtiene encabezados sin recorrer dimensiones fantasma del libro.

    Algunos archivos ``TrámitesCRT.xlsx`` conservan formato en una celda muy
    lejana. OpenPyXL refleja esa celda en ``max_row``/``max_column`` aunque no
    contenga datos, y recorrer toda esa dimensión puede convertir una
    reconciliación de segundos en horas. Para hojas normales usamos las celdas
    materializadas; para hojas ``read_only`` usamos únicamente la primera fila.
    """
    cells = getattr(ws, "_cells", None)
    if isinstance(cells, dict):
        encabezados = {}
        for (row, col), cell in cells.items():
            if row != 1:
                continue
            text = _texto(cell.value)
            if text:
                encabezados[text] = col
        if encabezados:
            return encabezados

    primera_fila = next(
        ws.iter_rows(min_row=1, max_row=1, values_only=True),
        (),
    )
    return {
        _texto(value): col
        for col, value in enumerate(primera_fila, start=1)
        if _texto(value)
    }


def _row_dict(ws, row_number: int, headers: dict[str, int]) -> dict[str, Any]:
    return {name: ws.cell(row_number, col).value for name, col in headers.items()}


def _es_fila_fantasma(values: list[Any], headers: dict[str, int]) -> bool:
    """Detecta filas creadas al procesar carpetas internas de ZIP como trámites.

    Solo se elimina una fila sin Registro cuando sus únicos valores útiles están
    en Memo/Volante y/o NOTAS_VICTOR. Las filas manuales con más información se
    conservan.
    """
    registro_col = headers.get("1711", 4) - 1
    if _registro(values[registro_col]):
        return False

    permitidas = {
        headers.get("Memo/Volante", 5) - 1,
        headers.get("NOTAS_VICTOR", 41) - 1,
    }
    utiles = {i for i, value in enumerate(values) if value not in (None, "")}
    return bool(utiles) and utiles.issubset(permitidas)


def reconciliar(
    tramites_path: str | Path,
    folios_path: str | Path,
    *,
    crear_backup: bool = True,
) -> dict[str, Any]:
    tramites_path = Path(tramites_path)
    folios_path = Path(folios_path)
    print(f"[RECON-EXCEL] Iniciando: maestro={tramites_path} fuente={folios_path}")
    if not tramites_path.exists():
        raise FileNotFoundError(f"No existe el Excel maestro: {tramites_path}")
    if not folios_path.exists():
        raise FileNotFoundError(f"No existe el Excel consolidado: {folios_path}")

    wb_t = openpyxl.load_workbook(tramites_path)
    if SHEET_TRAMITES not in wb_t.sheetnames:
        wb_t.close()
        raise KeyError(f"No existe la hoja '{SHEET_TRAMITES}' en {tramites_path}")
    ws_t = wb_t[SHEET_TRAMITES]
    headers_t = _headers(ws_t)

    required = {
        "1711", "Memo/Volante", "Solicitante Promovente", "Representante Legal",
        "Asunto", "Tipo Trámite", "Fecha de creación", "FECHA LÍMITE", "Ruta",
    }
    missing_headers = sorted(required - set(headers_t))
    if missing_headers:
        wb_t.close()
        raise ValueError(f"Faltan columnas en TrámitesCRT.xlsx: {', '.join(missing_headers)}")

    wb_f = openpyxl.load_workbook(folios_path, read_only=True, data_only=True)
    if SHEET_FOLIOS not in wb_f.sheetnames:
        wb_f.close()
        wb_t.close()
        raise KeyError(f"No existe la hoja '{SHEET_FOLIOS}' en {folios_path}")
    ws_f = wb_f[SHEET_FOLIOS]
    headers_f = _headers(ws_f)

    source_rows: list[dict[str, Any]] = []
    source_seen: set[str] = set()
    source_duplicates: list[str] = []
    max_source_col = max(headers_f.values(), default=0)
    for row_values in ws_f.iter_rows(
        min_row=2,
        max_col=max_source_col or None,
        values_only=True,
    ):
        item = {
            name: row_values[col - 1] if col <= len(row_values) else None
            for name, col in headers_f.items()
        }
        registro = _registro(item.get("registro") or item.get("metadata_satys.registro"))
        if not registro:
            continue
        if registro in source_seen:
            source_duplicates.append(registro)
            continue
        source_seen.add(registro)
        item["registro"] = registro
        source_rows.append(item)
    wb_f.close()
    print(f"[RECON-EXCEL] Fuente consolidada: {len(source_rows)} registro(s) CRT único(s)")

    # Indexar filas existentes por Registro sin reconstruir/reordenar el libro.
    existing_by_record: dict[str, int] = {}
    target_duplicates: list[str] = []
    manual_rows = 0
    phantom_detected = 0
    max_col = max(headers_t.values())

    cells_t = getattr(ws_t, "_cells", {})
    if isinstance(cells_t, dict):
        filas_con_datos = sorted({
            row
            for (row, col), cell in cells_t.items()
            if row >= 2 and col <= max_col and cell.value not in (None, "")
        })
    else:
        filas_con_datos = list(range(2, ws_t.max_row + 1))

    for row_num in filas_con_datos:
        values = [ws_t.cell(row_num, col).value for col in range(1, max_col + 1)]
        registro = _registro(values[headers_t["1711"] - 1])
        if registro:
            if registro in existing_by_record:
                target_duplicates.append(registro)
            else:
                existing_by_record[registro] = row_num
        elif _es_fila_fantasma(values, headers_t):
            # Fail-safe: se reporta, pero una reconciliación automática no borra filas.
            phantom_detected += 1
        elif any(value not in (None, "") for value in values):
            manual_rows += 1

    print(
        f"[RECON-EXCEL] Maestro real: {len(filas_con_datos)} fila(s) con datos, "
        f"{max_col} columna(s) con encabezado; dimensión declarada por Excel: "
        f"{ws_t.max_row}x{ws_t.max_column}"
    )

    appended = 0
    updated = 0
    format_headers = [f"R{i:03d}" for i in range(1, 28)]
    source_records = {item["registro"] for item in source_rows}

    def ruta_revision(value: Any) -> bool:
        ruta = _ruta_desde_output(value)
        partes = [p for p in ruta.split("\\") if p]
        if partes and re.fullmatch(r"2026Q[34]", partes[0], re.IGNORECASE):
            partes = partes[1:]
        return bool(partes) and partes[0].casefold() in {"_sin_operador", "sin_operador_correo"}

    for item in source_rows:
        registro = item["registro"]
        fila = existing_by_record.get(registro)
        fila_nueva = fila is None
        if fila_nueva:
            fila = ws_t.max_row + 1
            existing_by_record[registro] = fila
            appended += 1
        else:
            updated += 1

        def actual(header: str) -> Any:
            col = headers_t.get(header)
            return ws_t.cell(fila, col).value if col else None

        def set_if_blank(header: str, value: Any) -> None:
            col = headers_t.get(header)
            if not col or value in (None, ""):
                return
            if fila_nueva or ws_t.cell(fila, col).value in (None, ""):
                ws_t.cell(fila, col).value = value

        asunto = _primero(item, "metadata_satys.asunto", "metadata_tramite_nuevo.asunto")
        tipo = _primero(item, "metadata_satys.tipo_tramite", "metadata_tramite_nuevo.tipo_tramite")
        fecha = _primero(
            item,
            "metadata_satys.fecha_registro",
            "metadata_satys.fecha_folio_opc",
            "metadata_tramite_nuevo.fecha_registro",
        )
        fecha_limite = _primero(
            item,
            "metadata_tramite_nuevo.plazo_atencion",
            "metadata_satys.plazo_atencion",
        )

        set_if_blank("1711", registro)
        set_if_blank("Memo/Volante", _primero(item, "folio", "metadata_satys.folio", "metadata_tramite_nuevo.folio"))

        sol_fuente = _primero(
            item,
            "metadata_satys.solicitante",
            "metadata_tramite_nuevo.solicitante",
            "nombre_operador",
            "metadata_satys.nombre_operador",
            "metadata_tramite_nuevo.nombre_operador",
        )
        if _valor_remitente_faltante(actual("Solicitante Promovente")) and not _valor_remitente_faltante(sol_fuente):
            set_if_blank("Solicitante Promovente", sol_fuente)

        rep_fuente = _primero(
            item,
            "representante_legal",
            "metadata_satys.representante_legal",
            "metadata_tramite_nuevo.representante_legal",
        )
        if _valor_remitente_faltante(actual("Representante Legal")) and not _valor_remitente_faltante(rep_fuente):
            set_if_blank("Representante Legal", rep_fuente)

        set_if_blank("Asunto", asunto)
        set_if_blank("Tipo Trámite", tipo)
        set_if_blank("Fecha de creación", fecha)
        set_if_blank("FECHA LÍMITE", fecha_limite)

        # Ruta: sólo completar vacío o promover _sin_operador -> canónica.
        nueva_ruta = _ruta_desde_output(item.get("output"))
        col_ruta = headers_t.get("Ruta")
        if col_ruta and nueva_ruta:
            ruta_actual = _ruta_desde_output(ws_t.cell(fila, col_ruta).value)
            if not ruta_actual:
                ws_t.cell(fila, col_ruta).value = nueva_ruta
            elif ruta_revision(ruta_actual) and not ruta_revision(nueva_ruta):
                ws_t.cell(fila, col_ruta).value = nueva_ruta
            elif ruta_actual.casefold() != nueva_ruta.casefold():
                print(
                    f"[RECON-EXCEL] Ruta preservada para {registro}: "
                    f"{ruta_actual} (propuesta ignorada: {nueva_ruta})"
                )

        # Formatos: sólo agregar marcas confirmadas; nunca limpiar histórico.
        formatos = {match.group(0).upper() for match in FORMATO_RE.finditer(_texto(asunto))}
        for header in format_headers:
            if header in formatos and header in headers_t:
                ws_t.cell(fila, headers_t[header]).value = 1

    target_only = sorted(set(existing_by_record) - source_records)
    routes_blank = 0
    col_ruta = headers_t.get("Ruta")
    if col_ruta:
        for registro in source_records:
            fila = existing_by_record.get(registro)
            if fila and not _ruta_desde_output(ws_t.cell(fila, col_ruta).value):
                routes_blank += 1

    # Guardado atómico y respaldo antes de sustituir el maestro.
    backup_path = None
    if crear_backup:
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_path = tramites_path.with_name(f"{tramites_path.stem}_backup_{stamp}{tramites_path.suffix}")
        shutil.copy2(tramites_path, backup_path)

    temp_path = tramites_path.with_name(f".{tramites_path.name}.tmp")
    print(f"[RECON-EXCEL] Guardando cambios conservadores en temporal atómico...")
    wb_t.save(temp_path)
    wb_t.close()
    reemplazar_desde_temporal(temp_path, tramites_path)
    print("[RECON-EXCEL] Reconciliación terminada y maestro sustituido correctamente.")

    valid_final = len(existing_by_record)
    return {
        "source_records": len(source_rows),
        "updated": updated,
        "appended": appended,
        "phantom_removed": 0,
        "phantom_detected_preserved": phantom_detected,
        "target_only_preserved": len(target_only),
        "manual_rows_preserved": manual_rows,
        "source_duplicates": sorted(set(source_duplicates)),
        "target_duplicates": sorted(set(target_duplicates)),
        "valid_final": valid_final,
        "routes_blank": routes_blank,
        "target_rows_scanned": len(filas_con_datos),
        "target_columns_scanned": max_col,
        "backup": str(backup_path) if backup_path else "",
        "output": str(tramites_path),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Reconcilia TrámitesCRT.xlsx desde Folios_Datos_Completos.xlsx")
    parser.add_argument("--tramites", default="TrámitesCRT.xlsx")
    parser.add_argument("--folios", default="output/Folios_Datos_Completos.xlsx")
    parser.add_argument("--sin-backup", action="store_true")
    args = parser.parse_args()

    result = reconciliar(args.tramites, args.folios, crear_backup=not args.sin_backup)
    print("Reconciliación terminada:")
    for key, value in result.items():
        print(f"  {key}: {value}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
