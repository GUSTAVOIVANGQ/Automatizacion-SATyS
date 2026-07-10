#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""
=============================================================
  SATyS CRT — Monitor diario de Registros nuevos
=============================================================

Objetivo:
  1. Entra a SATyS con extraer_registros_documentos.py.
  2. Extrae todos los valores de la columna "Registro" en Documentos en Proceso.
  3. Lee TrámitesCRT.xlsx y toma como evidencia la columna cuyo encabezado es 1711.
  4. Genera registros.txt SOLO con registros nuevos no encontrados en Excel.
  5. Ejecuta main_procesar.py --archivo-registro registros.txt.
  6. Guarda logs, resumen JSON y muestra notificación de Windows cuando es posible.

Uso manual recomendado:
  .\python-3.11.9-embed-amd64\python.exe automatizar_registros_diario.py --headless --workers 6

Primera prueba visible:
  .\python-3.11.9-embed-amd64\python.exe automatizar_registros_diario.py --visible --workers 1
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
import traceback
from datetime import datetime
from pathlib import Path
from typing import Iterable

from proceso_lock import ProcesoLock, LockOcupadoError

try:
    import notificar_email as _email_mod
    _EMAIL_DISPONIBLE = True
except Exception:
    _EMAIL_DISPONIBLE = False

REGISTRO_RE = re.compile(r"\b[A-Z]{2,8}\d{2}-\d{3,}\b", re.IGNORECASE)

PROJECT_DIR = Path(__file__).resolve().parent
PYTHON_EXE_DEFAULT = PROJECT_DIR / "python-3.11.9-embed-amd64" / "python.exe"
EXTRAER_SCRIPT_DEFAULT = PROJECT_DIR / "extraer_registros_documentos.py"
MAIN_SCRIPT_DEFAULT = PROJECT_DIR / "main_procesar.py"
EXCEL_DEFAULT = PROJECT_DIR / "TrámitesCRT.xlsx"
REGISTROS_LATEST_DEFAULT = PROJECT_DIR / "registros.txt"
REGISTROS_DIR_DEFAULT = PROJECT_DIR / "registros_diarios"
LOG_DIR_DEFAULT = PROJECT_DIR / "logs"
SHEET_DEFAULT = "Turnados recibidos"
HEADER_REGISTRO_DEFAULT = "1711"


def normalizar_registro(valor: object) -> str:
    """Devuelve un Registro CRT normalizado o cadena vacía si no parece Registro."""
    if valor is None:
        return ""
    texto = str(valor).replace("\u00a0", " ").strip().upper()
    texto = re.sub(r"\s+", "", texto)
    m = REGISTRO_RE.search(texto)
    return m.group(0).upper() if m else ""


def unicos_preservando_orden(items: Iterable[str]) -> list[str]:
    vistos: set[str] = set()
    salida: list[str] = []
    for item in items:
        item = normalizar_registro(item)
        if item and item not in vistos:
            vistos.add(item)
            salida.append(item)
    return salida


def leer_registros_txt(path: Path) -> list[str]:
    if not path.exists():
        return []
    texto = path.read_text(encoding="utf-8-sig", errors="replace")
    return unicos_preservando_orden(REGISTRO_RE.findall(texto))


def validar_txt_un_registro_por_linea(path: Path, esperados: int) -> dict:
    """
    Valida que el TXT que consumirá main_procesar.py no quede como una sola línea gigante.
    """
    if not path.exists():
        return {"ok": False, "error": f"No existe el TXT: {path}"}

    texto = path.read_text(encoding="utf-8-sig", errors="replace")
    registros = unicos_preservando_orden(REGISTRO_RE.findall(texto))
    lineas_con_registro = [
        linea for linea in texto.splitlines()
        if normalizar_registro(linea)
    ]

    lineas_multiples = []
    for idx, linea in enumerate(texto.splitlines(), start=1):
        encontrados = REGISTRO_RE.findall(linea)
        if len(encontrados) > 1:
            lineas_multiples.append(idx)

    return {
        "ok": len(registros) == esperados and len(lineas_multiples) == 0,
        "registros_detectados": len(registros),
        "lineas_con_registro": len(lineas_con_registro),
        "esperados": esperados,
        "lineas_con_multiples_registros": lineas_multiples[:20],
    }


def guardar_txt_lineas(path: Path, registros: list[str]) -> None:
    """
    Guarda un registro por línea.

    IMPORTANTE:
    main_procesar.py debe recibir el TXT con saltos de línea reales.
    Antes se guardaba con espacios (" ".join), y eso provocaba que
    main_procesar.py leyera todos los Registros como 1 solo elemento.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    registros = unicos_preservando_orden(registros)
    contenido = "\n".join(registros)
    if contenido:
        contenido += "\n"
    path.write_text(contenido, encoding="utf-8")


def cargar_registros_procesados_excel(excel_path: Path, sheet_name: str, header_registro: str) -> tuple[set[str], dict]:
    """
    Busca la columna cuyo encabezado es 1711 y regresa todos los registros ya procesados.
    El Excel enviado tiene el encabezado 1711 en la hoja 'Turnados recibidos'.
    """
    try:
        import openpyxl  # dependencia ya usada por Parte4_excel.py
    except ImportError as exc:
        raise RuntimeError(
            "No se pudo importar openpyxl. Instálalo en el Python portable o revisa que Parte4_excel.py funcione."
        ) from exc

    if not excel_path.exists():
        raise FileNotFoundError(f"No existe el Excel de evidencia: {excel_path}")

    wb = openpyxl.load_workbook(excel_path, read_only=True, data_only=True)
    if sheet_name in wb.sheetnames:
        ws = wb[sheet_name]
    else:
        ws = wb[wb.sheetnames[0]]

    header_col = None
    header_row = None
    header_target = str(header_registro).strip()

    # Normalmente está en la primera fila, pero se busca en las primeras 20 por seguridad.
    max_scan_row = min(ws.max_row or 1, 20)
    for row in ws.iter_rows(min_row=1, max_row=max_scan_row):
        for cell in row:
            value = cell.value
            if value is None:
                continue
            value_text = str(value).strip()
            if value_text == header_target or value == int(header_target) if header_target.isdigit() else value_text == header_target:
                header_col = cell.column
                header_row = cell.row
                break
        if header_col is not None:
            break

    if header_col is None:
        # Fallback controlado: en el Excel revisado la columna 1711 corresponde a D.
        header_col = 4
        header_row = 1

    procesados: set[str] = set()
    for row in ws.iter_rows(min_row=(header_row or 1) + 1, min_col=header_col, max_col=header_col):
        registro = normalizar_registro(row[0].value)
        if registro:
            procesados.add(registro)

    info = {
        "excel": str(excel_path),
        "sheet": ws.title,
        "header_registro": header_registro,
        "header_row": header_row,
        "header_col": header_col,
        "total_procesados_excel": len(procesados),
    }
    wb.close()
    return procesados, info


def ejecutar_comando(cmd: list[str], cwd: Path, log_path: Path, titulo: str) -> int:
    """Ejecuta un comando mostrando y guardando stdout/stderr en un log."""
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("a", encoding="utf-8", errors="replace") as log_file:
        separador = "=" * 90
        log_file.write(f"\n{separador}\n{titulo}\n{datetime.now().isoformat()}\n")
        log_file.write("CMD: " + " ".join(f'\"{x}\"' if " " in str(x) else str(x) for x in cmd) + "\n")
        log_file.write(separador + "\n")
        log_file.flush()

        print(f"\n{separador}\n{titulo}\n{separador}")
        print("CMD:", " ".join(str(x) for x in cmd))

        proc = subprocess.Popen(
            [str(x) for x in cmd],
            cwd=str(cwd),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
        )

        assert proc.stdout is not None
        for line in proc.stdout:
            print(line, end="")
            log_file.write(line)
            log_file.flush()

        rc = proc.wait()
        log_file.write(f"\n[{titulo}] return_code={rc}\n")
        log_file.flush()
        print(f"\n[{titulo}] return_code={rc}")
        return rc


def ps_quote(text: str) -> str:
    return "'" + text.replace("'", "''") + "'"


def notificar_windows(titulo: str, mensaje: str, habilitado: bool = True) -> bool:
    """Notificación tipo globo en Windows, sin dependencias externas. No bloquea el proceso."""
    if not habilitado or os.name != "nt":
        return False
    try:
        mensaje = mensaje[:250]
        script = f"""
Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing
$n = New-Object System.Windows.Forms.NotifyIcon
$n.Icon = [System.Drawing.SystemIcons]::Information
$n.BalloonTipTitle = {ps_quote(titulo)}
$n.BalloonTipText = {ps_quote(mensaje)}
$n.Visible = $true
$n.ShowBalloonTip(10000)
Start-Sleep -Seconds 11
$n.Dispose()
""".strip()
        subprocess.Popen(
            ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-WindowStyle", "Hidden", "-Command", script],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        return True
    except Exception:
        return False


def construir_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Monitor diario: extrae Registros SATyS, compara contra TrámitesCRT.xlsx y procesa solo nuevos."
    )
    parser.add_argument("--python", dest="python_exe", type=Path, default=PYTHON_EXE_DEFAULT,
                        help="Ruta al python.exe portable.")
    parser.add_argument("--extraer-script", type=Path, default=EXTRAER_SCRIPT_DEFAULT,
                        help="Ruta a extraer_registros_documentos.py.")
    parser.add_argument("--main-script", type=Path, default=MAIN_SCRIPT_DEFAULT,
                        help="Ruta a main_procesar.py.")
    parser.add_argument("--excel", type=Path, default=EXCEL_DEFAULT,
                        help="Ruta a TrámitesCRT.xlsx.")
    parser.add_argument("--sheet", default=SHEET_DEFAULT,
                        help="Hoja del Excel donde se encuentra la columna 1711.")
    parser.add_argument("--header-registro", default=HEADER_REGISTRO_DEFAULT,
                        help="Encabezado de la columna de registros ya procesados.")
    parser.add_argument("--registros-latest", type=Path, default=REGISTROS_LATEST_DEFAULT,
                        help="TXT que consumirá main_procesar.py. Default: registros.txt")
    parser.add_argument("--registros-dir", type=Path, default=REGISTROS_DIR_DEFAULT,
                        help="Carpeta donde se guardan copias históricas de TXT.")
    parser.add_argument("--logs-dir", type=Path, default=LOG_DIR_DEFAULT,
                        help="Carpeta donde se guardan logs y resúmenes.")
    parser.add_argument("--workers", type=int, default=6,
                        help="Workers de Playwright para main_procesar.py. Recomendado: 6.")
    parser.add_argument("--timeout-registro", type=int, default=900,
                        help="Timeout duro por Registro en segundos. Si un Registro se traba, se mata su proceso hijo y sigue el lote.")
    parser.add_argument("--reintentos-registro", type=int, default=2,
                        help="Reintentos automáticos solo para registros incompletos. 2 = hasta 3 intentos totales.")
    parser.add_argument("--workers-reintento", type=int, default=2,
                        help="Workers usados en reintentos de registros fallidos/incompletos. Default: 2.")
    parser.add_argument("--headless", action="store_true", default=True,
                        help="Ejecuta Playwright sin navegador visible. Default: activo.")
    parser.add_argument("--visible", action="store_true",
                        help="Fuerza navegador visible para depuración; desactiva --headless.")
    parser.add_argument("--max-paginas", type=int, default=100,
                        help="Máximo de páginas DataTables al extraer registros.")
    parser.add_argument("--timeout-tabla", type=int, default=60,
                        help="Tiempo máximo en segundos para esperar que cargue la tabla de Documentos en Proceso.")
    parser.add_argument("--no-procesar", action="store_true",
                        help="Solo genera TXT de nuevos registros; no ejecuta main_procesar.py.")
    parser.add_argument("--sin-notificacion", action="store_true",
                        help="No intenta mostrar notificación de Windows.")
    return parser


def main() -> int:
    args = construir_parser().parse_args()
    headless = bool(args.headless and not args.visible)

    fecha = datetime.now().strftime("%Y%m%d_%H%M%S")
    args.registros_dir.mkdir(parents=True, exist_ok=True)
    args.logs_dir.mkdir(parents=True, exist_ok=True)

    log_path = args.logs_dir / f"monitor_registros_{fecha}.log"
    resumen_json = args.logs_dir / f"monitor_registros_{fecha}.json"
    resumen_latest = args.logs_dir / "monitor_registros_ultimo.json"

    registros_satys_hist = args.registros_dir / f"registros_satys_{fecha}.txt"
    registros_nuevos_hist = args.registros_dir / f"registros_nuevos_{fecha}.txt"

    resumen: dict = {
        "fecha_ejecucion": datetime.now().isoformat(),
        "headless": headless,
        "workers": args.workers,
        "timeout_registro_segundos": args.timeout_registro,
        "reintentos_registro": args.reintentos_registro,
        "workers_reintento": args.workers_reintento,
        "timeout_tabla_segundos": args.timeout_tabla,
        "paths": {
            "project_dir": str(PROJECT_DIR),
            "excel": str(args.excel),
            "registros_latest": str(args.registros_latest),
            "registros_satys_hist": str(registros_satys_hist),
            "registros_nuevos_hist": str(registros_nuevos_hist),
            "log": str(log_path),
        },
        "ok": False,
        "errores": [],
    }

    # ──── Bloqueo compartido: evita que 2+ laptops corran el monitor a la vez ────
    # Cubre también a extraer_registros_documentos.py y main_procesar.py, que se
    # lanzan como subprocesos y heredan este mismo bloqueo automáticamente.
    lock = ProcesoLock(proceso="automatizar_registros_diario.py")
    try:
        lock.adquirir()
    except LockOcupadoError as exc:
        # No es un error real del monitor: simplemente otra laptop ya está
        # trabajando. Se omite esta corrida sin marcarla como fallo.
        resumen["ok"] = True
        resumen["omitido_por_bloqueo"] = True
        resumen["mensaje"] = f"Se omitió esta corrida: {exc}"
        resumen["fecha_fin"] = datetime.now().isoformat()
        print(f"🔒 {exc}")
        print("   Se omite esta corrida del monitor diario en esta laptop.")
        try:
            resumen_json.write_text(json.dumps(resumen, ensure_ascii=False, indent=2), encoding="utf-8")
            resumen_latest.write_text(json.dumps(resumen, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception:
            pass
        notificar_windows(
            "SATyS CRT — corrida diaria omitida",
            str(exc),
            habilitado=not args.sin_notificacion,
        )
        return 0

    try:
        if not args.python_exe.exists():
            raise FileNotFoundError(f"No existe python.exe: {args.python_exe}")
        if not args.extraer_script.exists():
            raise FileNotFoundError(f"No existe script extractor: {args.extraer_script}")
        if not args.main_script.exists():
            raise FileNotFoundError(f"No existe main_procesar.py: {args.main_script}")

        # 1) Extraer todos los registros visibles desde SATyS.
        cmd_extraer = [
            str(args.python_exe),
            str(args.extraer_script),
            "--output", str(registros_satys_hist),
            "--separador", "linea",
            "--max-paginas", str(args.max_paginas),
            "--timeout-tabla", str(args.timeout_tabla),
            "--modo-anios", "todos",
        ]
        cmd_extraer.append("--headless" if headless else "--visible")
        rc_extraer = ejecutar_comando(cmd_extraer, PROJECT_DIR, log_path, "1) EXTRAER REGISTROS DESDE SATyS")
        resumen["return_code_extraer"] = rc_extraer
        if rc_extraer != 0:
            raise RuntimeError(f"extraer_registros_documentos.py terminó con código {rc_extraer}")

        registros_satys = leer_registros_txt(registros_satys_hist)
        resumen["total_registros_satys"] = len(registros_satys)
        resumen["primeros_registros_satys"] = registros_satys[:15]
        resumen_extraer_path = registros_satys_hist.with_suffix(registros_satys_hist.suffix + ".json")
        if resumen_extraer_path.exists():
            try:
                resumen["extraccion_satys"] = json.loads(
                    resumen_extraer_path.read_text(encoding="utf-8-sig", errors="replace")
                )
            except Exception as exc:
                resumen["errores"].append(f"No se pudo leer resumen del extractor: {exc}")
        if not registros_satys:
            raise RuntimeError("No se extrajo ningún Registro desde SATyS. Revisa login, red CRT o selectores de tabla.")

        # 2) Leer evidencia Excel, columna 1711.
        procesados_excel, excel_info = cargar_registros_procesados_excel(
            args.excel, args.sheet, args.header_registro
        )
        resumen["excel_info"] = excel_info

        # 3) Comparar y guardar nuevos.
        nuevos_excel = [registro for registro in registros_satys if registro not in procesados_excel]

        # 3b) También incluir registros que ya están en el Excel pero tienen
        #     carpeta sin archivos reales (solo los JSONs generados por el programa).
        #     Esto garantiza que los re-intentos ocurran en cada corrida diaria.
        DESCARGA_BASE_DIARIO = PROJECT_DIR / "descargas"
        _JSON_GEN_DIARIO = {"metadata_completo.json", "metadata_satys.json", "metadata_tramite_nuevo.json"}

        def _descarga_incompleta_diario(reg: str) -> bool:
            """True si el registro no tiene archivos reales descargados."""
            carpeta_d = DESCARGA_BASE_DIARIO / reg
            if not carpeta_d.exists():
                return True
            archivos_d = [
                f for f in carpeta_d.glob("*")
                if f.is_file() and f.name not in _JSON_GEN_DIARIO
            ]
            return not bool(archivos_d)

        # Registros en Excel pero con descarga incompleta (no incluidos ya en nuevos_excel)
        incompletos_en_excel = [
            reg for reg in registros_satys
            if reg in procesados_excel and _descarga_incompleta_diario(reg)
        ]

        nuevos = unicos_preservando_orden(nuevos_excel + incompletos_en_excel)

        if incompletos_en_excel:
            print(f"⚠️  Registros en Excel pero con descarga incompleta (se re-intentarán): {len(incompletos_en_excel)}")
            print("   ", ", ".join(incompletos_en_excel[:30]) + ("..." if len(incompletos_en_excel) > 30 else ""))

        guardar_txt_lineas(registros_nuevos_hist, nuevos)
        guardar_txt_lineas(args.registros_latest, nuevos)

        validacion_txt = validar_txt_un_registro_por_linea(args.registros_latest, len(nuevos))
        resumen["validacion_registros_txt"] = validacion_txt
        if nuevos and not validacion_txt.get("ok"):
            raise RuntimeError(
                "El TXT de registros nuevos no quedó en formato de un Registro por línea: "
                f"{validacion_txt}"
            )

        resumen["total_procesados_excel"] = len(procesados_excel)
        resumen["total_nuevos"] = len(nuevos)
        resumen["total_nuevos_excel"] = len(nuevos_excel)
        resumen["total_incompletos_reintento"] = len(incompletos_en_excel)
        resumen["registros_nuevos"] = nuevos

        print("\n" + "=" * 90)
        print("RESULTADO DE COMPARACIÓN")
        print("=" * 90)
        print(f"Registros en SATyS:           {len(registros_satys)}")
        print(f"Ya procesados en Excel:        {len(procesados_excel)}")
        print(f"Nuevos (no en Excel):          {len(nuevos_excel)}")
        print(f"Re-intentos (descarga incompleta): {len(incompletos_en_excel)}")
        print(f"Total a procesar:              {len(nuevos)}")
        print(f"TXT nuevos para main:          {args.registros_latest}")
        print(f"Copia histórica nuevos:        {registros_nuevos_hist}")
        if nuevos:
            print("Nuevos:", ", ".join(nuevos[:50]) + ("..." if len(nuevos) > 50 else ""))

        if not nuevos:
            resumen["ok"] = True
            resumen["mensaje"] = "No hay registros nuevos. No se ejecutó main_procesar.py."
            notificar_windows(
                "SATyS CRT — sin registros nuevos",
                f"Se revisaron {len(registros_satys)} registros; todos existen en TrámitesCRT.xlsx.",
                habilitado=not args.sin_notificacion,
            )
            return 0

        # 4) Ejecutar main_procesar.py por Registro.
        if args.no_procesar:
            resumen["ok"] = True
            resumen["mensaje"] = "Se generó TXT de nuevos registros, pero no se procesó por --no-procesar."
            notificar_windows(
                "SATyS CRT — registros nuevos detectados",
                f"{len(nuevos)} registro(s) nuevo(s). TXT: {args.registros_latest.name}",
                habilitado=not args.sin_notificacion,
            )
            return 0

        cmd_main = [
            str(args.python_exe),
            str(args.main_script),
            "--archivo-registro", str(args.registros_latest),
            "--workers", str(args.workers),
            "--timeout-registro", str(args.timeout_registro),
            "--reintentos-registro", str(args.reintentos_registro),
            "--workers-reintento", str(args.workers_reintento),
        ]
        if headless:
            cmd_main.append("--headless")

        rc_main = ejecutar_comando(cmd_main, PROJECT_DIR, log_path, "2) PROCESAR REGISTROS NUEVOS")
        resumen["return_code_main"] = rc_main

        fallidos_latest = PROJECT_DIR / "registros_fallidos" / "registros_fallidos_latest.txt"
        fallidos = leer_registros_txt(fallidos_latest) if fallidos_latest.exists() else []
        resumen["registros_fallidos_controlados"] = fallidos
        resumen["total_fallidos_controlados"] = len(fallidos)
        resumen["ok"] = rc_main == 0
        resumen["mensaje"] = (
            f"Procesados {len(nuevos)} registro(s) nuevo(s). Código main_procesar.py: {rc_main}. "
            f"Fallidos controlados: {len(fallidos)}."
        )

        notificar_windows(
            "SATyS CRT — proceso diario finalizado",
            f"Nuevos: {len(nuevos)} | Fallidos controlados: {len(fallidos)} | main_procesar.py código: {rc_main} | Log: {log_path.name}",
            habilitado=not args.sin_notificacion,
        )

        # ── Notificación por correo electrónico ──────────────────────────────
        if _EMAIL_DISPONIBLE:
            # main_procesar.py guarda el log en descargas/procesamiento_log_registros.json
            log_json_path = PROJECT_DIR / "descargas" / "procesamiento_log_registros.json"
            _email_mod.enviar_desde_log_json(log_json_path)
        else:
            print("\n⚠️  Módulo notificar_email no disponible; correo no enviado.")
        # ─────────────────────────────────────────────────────────────────────

        return rc_main

    except Exception as exc:
        resumen["ok"] = False
        resumen["errores"].append(str(exc))
        resumen["traceback"] = traceback.format_exc()
        print("\nERROR EN MONITOR DIARIO:", exc)
        print(resumen["traceback"])
        notificar_windows(
            "SATyS CRT — error en monitor diario",
            str(exc),
            habilitado=not args.sin_notificacion,
        )
        # Correo de aviso de fallo
        if _EMAIL_DISPONIBLE:
            _email_mod.enviar_notificacion(
                total_registros=resumen.get("total_nuevos", 0),
                exitosos=0,
                sin_operador=0,
                errores=resumen.get("total_nuevos", 0),
                registros=[],
                fecha_ejecucion=resumen.get("fecha_ejecucion"),
            )
        return 1

    finally:
        try:
            resumen["fecha_fin"] = datetime.now().isoformat()
            resumen_json.write_text(json.dumps(resumen, ensure_ascii=False, indent=2), encoding="utf-8")
            resumen_latest.write_text(json.dumps(resumen, ensure_ascii=False, indent=2), encoding="utf-8")
            print(f"\nResumen JSON: {resumen_json}")
            print(f"Último resumen: {resumen_latest}")
            print(f"Log completo: {log_path}")
        except Exception:
            pass


if __name__ == "__main__":
    raise SystemExit(main())
