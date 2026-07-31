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
  6. Guarda logs, resumen JSON y estado vivo para monitoreo.

Uso manual recomendado en Linux/RHEL:
  /data/gustavo.garcia/satys/venv/bin/python automatizar_registros_diario.py --headless --workers 10

Primera prueba visible en una estación con navegador:
  python automatizar_registros_diario.py --visible --workers 1
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import signal
import time
import traceback
from datetime import datetime
from pathlib import Path
from typing import Iterable

from proceso_lock import ProcesoLock, LockOcupadoError
from estado_ejecucion import EstadoEjecucion
from estado_descargas import registro_esta_completo
from configuracion_local import carpeta_compartida, configuracion_procesamiento, ruta_configurada
from sincronizacion_depi import sincronizar_salidas

try:
    import notificar_email as _email_mod
    _EMAIL_DISPONIBLE = True
except Exception:
    _EMAIL_DISPONIBLE = False

REGISTRO_RE = re.compile(r"\b[A-Z]{2,8}\d{2}-\d{3,}\b", re.IGNORECASE)

PROJECT_DIR = Path(__file__).resolve().parent
PYTHON_EXE_DEFAULT = Path(os.getenv("SATYS_PYTHON", sys.executable))
EXTRAER_SCRIPT_DEFAULT = PROJECT_DIR / "extraer_registros_documentos.py"
MAIN_SCRIPT_DEFAULT = PROJECT_DIR / "main_procesar.py"
RECONCILIAR_SCRIPT_DEFAULT = PROJECT_DIR / "reconciliar_metadata_global.py"
EXCEL_DEFAULT = ruta_configurada("excel", "TrámitesCRT.xlsx")
REGISTROS_LATEST_DEFAULT = PROJECT_DIR / "registros.txt"
REGISTROS_DIR_DEFAULT = PROJECT_DIR / "registros_diarios"
LOG_DIR_DEFAULT = PROJECT_DIR / "logs"
SHEET_DEFAULT = "Turnados recibidos"
HEADER_REGISTRO_DEFAULT = "1711"
PROCESAMIENTO_CFG = configuracion_procesamiento()
WORKERS_DEFAULT = int(PROCESAMIENTO_CFG.get("workers", 10))
TIMEOUT_REGISTRO_DEFAULT = int(PROCESAMIENTO_CFG.get("timeout_registro", 900))
REINTENTOS_REGISTRO_DEFAULT = int(PROCESAMIENTO_CFG.get("reintentos_registro", 2))
WORKERS_REINTENTO_DEFAULT = int(PROCESAMIENTO_CFG.get("workers_reintento", 2))
TIMEOUT_TABLA_DEFAULT = 120
INTENTOS_ANIO_EXTRACCION_DEFAULT = 3
INTENTOS_PAGINA_EXTRACCION_DEFAULT = 3
# Reintentos exclusivos de la etapa inicial de consulta a SATyS. No modifican
# los reintentos por Registro de main_procesar.py.
REINTENTOS_EXTRACCION_DEFAULT = max(0, int(PROCESAMIENTO_CFG.get("reintentos_extraccion", 2)))
ESPERA_REINTENTO_EXTRACCION_DEFAULT = 0


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


def ejecutar_comando(
    cmd: list[str],
    cwd: Path,
    log_path: Path,
    titulo: str,
    estado: EstadoEjecucion | None = None,
    etapa: str = "",
) -> int:
    """Ejecuta un comando mostrando/guardando salida y actualizando estado vivo."""
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("a", encoding="utf-8", errors="replace") as log_file:
        separador = "=" * 90
        log_file.write(f"\n{separador}\n{titulo}\n{datetime.now().isoformat()}\n")
        log_file.write("CMD: " + " ".join(f'\"{x}\"' if " " in str(x) else str(x) for x in cmd) + "\n")
        log_file.write(separador + "\n")
        log_file.flush()

        print(f"\n{separador}\n{titulo}\n{separador}")
        print("CMD:", " ".join(str(x) for x in cmd))

        if estado is not None:
            estado.actualizar(stage=etapa or titulo, comando=" ".join(str(x) for x in cmd))

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
        ultima_actualizacion_estado = 0.0
        for line in proc.stdout:
            print(line, end="")
            log_file.write(line)
            log_file.flush()
            if estado is not None and time.time() - ultima_actualizacion_estado >= 5:
                estado.actualizar(stage=etapa or titulo, ultima_linea=line.strip()[:500])
                ultima_actualizacion_estado = time.time()

        rc = proc.wait()
        if estado is not None:
            estado.actualizar(stage=f"{etapa or titulo}: terminado", return_code=rc)
        log_file.write(f"\n[{titulo}] return_code={rc}\n")
        log_file.flush()
        print(f"\n[{titulo}] return_code={rc}")
        return rc


class ExtraccionSatysAgotada(RuntimeError):
    """Error con el historial de todos los intentos de extracción agotados."""

    def __init__(self, mensaje: str, historial: list[dict]):
        super().__init__(mensaje)
        self.historial = historial


def limpiar_salida_extraccion(output_path: Path) -> None:
    """Elimina la salida de un intento anterior para evitar aceptar datos obsoletos."""
    candidatos = (
        output_path,
        output_path.with_suffix(output_path.suffix + ".json"),
    )
    for candidato in candidatos:
        try:
            candidato.unlink()
        except FileNotFoundError:
            pass


def validar_resumen_extractor(output_path: Path, registros: list[str]) -> dict:
    """Valida el certificado JSON del extractor contra el TXT publicado."""
    resumen_path = output_path.with_suffix(output_path.suffix + ".json")
    resultado = {
        "ok": False,
        "path": str(resumen_path),
        "vacio_confirmado": False,
        "error": "",
        "resumen": None,
    }
    if not resumen_path.exists():
        resultado["error"] = "falta el resumen JSON del extractor"
        return resultado
    try:
        resumen = json.loads(resumen_path.read_text(encoding="utf-8-sig", errors="replace"))
    except Exception as exc:
        resultado["error"] = f"resumen JSON ilegible: {exc}"
        return resultado
    resultado["resumen"] = resumen
    if resumen.get("estado") != "COMPLETO" or resumen.get("integridad") != "VALIDADA":
        resultado["error"] = (
            f"estado/integridad no válidos: estado={resumen.get('estado')!r}, "
            f"integridad={resumen.get('integridad')!r}"
        )
        return resultado
    if int(resumen.get("total_registros", -1)) != len(registros):
        resultado["error"] = (
            f"TXT y resumen no coinciden: TXT={len(registros)}, "
            f"resumen={resumen.get('total_registros')!r}"
        )
        return resultado

    por_anio = resumen.get("por_anio")
    if not isinstance(por_anio, list) or not por_anio:
        resultado["error"] = "el resumen no contiene detalle por año"
        return resultado
    estados_validos = {"ENCONTRADOS_COMPLETOS", "VACIO_CONFIRMADO"}
    total_filas = 0
    todos_vacios = True
    for item in por_anio:
        estado = item.get("estado")
        if estado not in estados_validos:
            resultado["error"] = f"año {item.get('anio')}: estado indeterminado {estado!r}"
            return resultado
        total = int(item.get("total_reportado_satys") or 0)
        filas = int(item.get("filas_leidas") or 0)
        guardados = int(item.get("total_guardados_anio") or 0)
        invalidas = int(item.get("filas_invalidas") or 0)
        if invalidas != 0:
            resultado["error"] = f"año {item.get('anio')}: {invalidas} fila(s) inválida(s)"
            return resultado
        if estado == "ENCONTRADOS_COMPLETOS":
            todos_vacios = False
            if total <= 0 or filas != total or guardados <= 0:
                resultado["error"] = (
                    f"año {item.get('anio')}: conciliación incompleta "
                    f"total={total}, filas={filas}, guardados={guardados}"
                )
                return resultado
        else:
            if total != 0 or filas != 0 or guardados != 0:
                resultado["error"] = (
                    f"año {item.get('anio')}: VACIO_CONFIRMADO inconsistente "
                    f"total={total}, filas={filas}, guardados={guardados}"
                )
                return resultado
        total_filas += filas

    if int(resumen.get("total_filas_satys", -1)) != total_filas:
        resultado["error"] = (
            f"total_filas_satys inconsistente: resumen={resumen.get('total_filas_satys')!r}, "
            f"suma={total_filas}"
        )
        return resultado
    vacio_declarado = bool(resumen.get("vacio_confirmado"))
    if vacio_declarado != todos_vacios:
        resultado["error"] = (
            f"bandera vacio_confirmado inconsistente: declarada={vacio_declarado}, calculada={todos_vacios}"
        )
        return resultado
    if todos_vacios and registros:
        resultado["error"] = "el resumen confirma vacío pero el TXT contiene registros"
        return resultado
    if not todos_vacios and not registros:
        resultado["error"] = "el resumen reporta filas pero el TXT quedó vacío"
        return resultado

    resultado["ok"] = True
    resultado["vacio_confirmado"] = todos_vacios
    return resultado


def extraer_registros_satys_con_reintentos(
    *,
    cmd_extraer: list[str],
    output_path: Path,
    cwd: Path,
    log_path: Path,
    estado: EstadoEjecucion | None,
    reintentos: int,
    espera_segundos: int,
) -> tuple[list[str], list[dict]]:
    """
    Ejecuta la extracción hasta obtener un resultado certificado y conciliado.

    Cada intento lanza un proceso nuevo de extraer_registros_documentos.py. Ese
    proceso abre su propio navegador y siempre lo cierra en su bloque ``finally``;
    por ello cada reintento empieza con un navegador limpio.

    Se reintenta cuando:
      * el extractor termina con código distinto de cero;
      * falta el resumen JSON de integridad; o
      * TXT y resumen no concilian.

    Un TXT vacío solo es válido si el resumen declara VACIO_CONFIRMADO para todos
    los años. ``reintentos=2`` significa un máximo de tres intentos totales. Una
    vez que el resultado queda certificado, el flujo regresa al procesamiento sin alterar
    Parte 1, Parte 2, Parte 3, Parte 4 ni sus reintentos por Registro.
    """
    reintentos = max(0, int(reintentos))
    espera_segundos = max(0, int(espera_segundos))
    total_intentos = reintentos + 1
    historial: list[dict] = []

    for numero_intento in range(1, total_intentos + 1):
        limpiar_salida_extraccion(output_path)
        titulo = (
            "1) EXTRAER REGISTROS DESDE SATyS "
            f"(intento {numero_intento}/{total_intentos})"
        )
        inicio = datetime.now().isoformat()
        if estado is not None:
            estado.actualizar(
                stage="extrayendo_registros_satys",
                intento_extraccion=numero_intento,
                total_intentos_extraccion=total_intentos,
            )

        rc = ejecutar_comando(
            cmd_extraer,
            cwd,
            log_path,
            titulo,
            estado=estado,
            etapa="extrayendo_registros_satys",
        )
        registros = leer_registros_txt(output_path) if rc == 0 else []
        validacion = validar_resumen_extractor(output_path, registros) if rc == 0 else {
            "ok": False, "vacio_confirmado": False, "error": "el extractor terminó con error"
        }
        motivo = "ok"
        if rc != 0:
            motivo = f"extractor terminó con código {rc}"
        elif not validacion.get("ok"):
            motivo = f"certificado de integridad inválido: {validacion.get('error')}"
        elif validacion.get("vacio_confirmado"):
            motivo = "VACIO_CONFIRMADO"

        detalle = {
            "intento": numero_intento,
            "total_intentos": total_intentos,
            "fecha_inicio": inicio,
            "fecha_fin": datetime.now().isoformat(),
            "return_code": rc,
            "total_registros": len(registros),
            "vacio_confirmado": bool(validacion.get("vacio_confirmado")),
            "integridad_ok": bool(validacion.get("ok")),
            "error_integridad": validacion.get("error", ""),
            "resultado": motivo,
        }
        historial.append(detalle)

        if rc == 0 and validacion.get("ok"):
            if registros:
                print(
                    f"✅ Extracción SATyS válida en intento {numero_intento}/{total_intentos}: "
                    f"{len(registros)} Registro(s), integridad conciliada."
                )
            else:
                print(
                    f"✅ Extracción SATyS válida en intento {numero_intento}/{total_intentos}: "
                    "cero Registros confirmado por DataTables y certificado JSON."
                )
            return registros, historial

        aviso = (
            f"⚠️  Extracción SATyS no válida en intento {numero_intento}/{total_intentos}: "
            f"{motivo}."
        )
        print(aviso)
        with log_path.open("a", encoding="utf-8", errors="replace") as log_file:
            log_file.write("\n" + aviso + "\n")

        if numero_intento < total_intentos:
            if estado is not None:
                estado.actualizar(
                    stage="esperando_reintento_extraccion_satys",
                    intento_extraccion=numero_intento,
                    siguiente_intento=numero_intento + 1,
                    espera_segundos=espera_segundos,
                    motivo_reintento=motivo,
                )
            if espera_segundos:
                print(
                    f"   El navegador del intento anterior ya terminó. "
                    f"Nuevo intento en {espera_segundos} segundo(s)..."
                )
                time.sleep(espera_segundos)
            else:
                print(
                    "   El navegador del intento anterior ya terminó. "
                    "Iniciando el siguiente intento de inmediato..."
                )

    ultimo = historial[-1] if historial else {}
    raise ExtraccionSatysAgotada(
        "No fue posible obtener Registros desde SATyS después de "
        f"{total_intentos} intento(s). Último resultado: "
        f"{ultimo.get('resultado', 'sin resultado')}; "
        f"return_code={ultimo.get('return_code', 'N/D')}; "
        f"registros={ultimo.get('total_registros', 0)}.",
        historial,
    )


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




def sincronizar_estado_diario_depi() -> None:
    """Sincroniza únicamente TrámitesCRT.xlsx, output/ y descargas/ hacia DEPI."""
    resultado = sincronizar_salidas(
        PROJECT_DIR,
        carpeta_compartida(),
    )
    for error in resultado.errores:
        print(f"⚠️  Sincronización diaria DEPI: {error}")


def ejecutar_reconciliacion_global(
    *,
    python_exe: Path,
    script: Path,
    excel: Path,
    log_path: Path,
    estado: EstadoEjecucion,
    sin_backup: bool = False,
) -> int:
    """Reconcilia siempre el Excel maestro desde todos los metadata JSON."""
    cmd = [
        str(python_exe),
        str(script),
        "--excel", str(excel),
        "--resumen-json", str(LOG_DIR_DEFAULT / "reconciliacion_global_ultimo.json"),
    ]
    if sin_backup:
        cmd.append("--sin-backup")
    return ejecutar_comando(
        cmd,
        PROJECT_DIR,
        log_path,
        "3) RECONCILIAR TRÁMITESCRT DESDE TODOS LOS METADATA",
        estado=estado,
        etapa="reconciliando_excel_global",
    )

def construir_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Monitor diario: extrae Registros SATyS, compara contra TrámitesCRT.xlsx y procesa solo nuevos."
    )
    parser.add_argument("--python", dest="python_exe", type=Path, default=PYTHON_EXE_DEFAULT,
                        help="Ruta al intérprete Python. Default: sys.executable o SATYS_PYTHON.")
    parser.add_argument("--extraer-script", type=Path, default=EXTRAER_SCRIPT_DEFAULT,
                        help="Ruta a extraer_registros_documentos.py.")
    parser.add_argument("--main-script", type=Path, default=MAIN_SCRIPT_DEFAULT,
                        help="Ruta a main_procesar.py.")
    parser.add_argument("--reconciliar-script", type=Path, default=RECONCILIAR_SCRIPT_DEFAULT,
                        help="Ruta a reconciliar_metadata_global.py.")
    parser.add_argument("--sin-reconciliacion-global", action="store_true",
                        help="Desactiva la reconciliación completa de TrámitesCRT.xlsx; solo diagnóstico.")
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
    parser.add_argument("--workers", type=int, default=WORKERS_DEFAULT,
                        help="Workers iniciales de Playwright. Configuración local: 10.")
    parser.add_argument("--timeout-registro", type=int, default=TIMEOUT_REGISTRO_DEFAULT,
                        help="Timeout duro por Registro en segundos. Si un Registro se traba, se mata su proceso hijo y sigue el lote.")
    parser.add_argument("--reintentos-registro", type=int, default=REINTENTOS_REGISTRO_DEFAULT,
                        help="Reintentos automáticos solo para registros incompletos. 2 = hasta 3 intentos totales.")
    parser.add_argument("--workers-reintento", type=int, default=WORKERS_REINTENTO_DEFAULT,
                        help="Workers usados en reintentos de registros fallidos/incompletos. Default: 2.")
    parser.add_argument("--reintentos-extraccion", type=int, default=REINTENTOS_EXTRACCION_DEFAULT,
                        help="Reintentos adicionales solo para extraer la lista inicial desde SATyS. 2 = hasta 3 intentos totales.")
    parser.add_argument("--espera-reintento-extraccion", type=int, default=ESPERA_REINTENTO_EXTRACCION_DEFAULT,
                        help="Segundos de espera entre intentos de extracción SATyS. Default: 0 (reintento inmediato).")
    parser.add_argument("--headless", action="store_true", default=True,
                        help="Ejecuta Playwright sin navegador visible. Default: activo.")
    parser.add_argument("--visible", action="store_true",
                        help="Fuerza navegador visible para depuración; desactiva --headless.")
    parser.add_argument("--max-paginas", type=int, default=100,
                        help="Máximo de páginas DataTables al extraer registros.")
    parser.add_argument("--timeout-tabla", type=int, default=TIMEOUT_TABLA_DEFAULT,
                        help="Tiempo máximo por año/página para esperar al menos un Registro. Default: 120 segundos.")
    parser.add_argument("--intentos-anio-extraccion", type=int, default=INTENTOS_ANIO_EXTRACCION_DEFAULT,
                        help="Intentos totales por año antes de fallar. Default: 3.")
    parser.add_argument("--intentos-pagina-extraccion", type=int, default=INTENTOS_PAGINA_EXTRACCION_DEFAULT,
                        help="Intentos para confirmar el avance de cada página. Default: 3.")
    parser.add_argument("--no-procesar", action="store_true",
                        help="Solo genera TXT de nuevos registros; no ejecuta main_procesar.py.")
    parser.add_argument("--sin-notificacion", action="store_true",
                        help="No intenta mostrar notificación de Windows.")
    parser.add_argument("--sin-email", action="store_true",
                        help="No envía correo al finalizar; útil para pruebas o despliegue inicial.")
    parser.add_argument("--estado-json", type=Path, default=LOG_DIR_DEFAULT / "estado_actual.json",
                        help="Archivo JSON de estado vivo para dashboard/monitoreo.")
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
    estado = EstadoEjecucion(args.estado_json, proceso="automatizar_registros_diario.py")

    registros_satys_hist = args.registros_dir / f"registros_satys_{fecha}.txt"
    registros_nuevos_hist = args.registros_dir / f"registros_nuevos_{fecha}.txt"

    resumen: dict = {
        "fecha_ejecucion": datetime.now().isoformat(),
        "headless": headless,
        "workers": args.workers,
        "timeout_registro_segundos": args.timeout_registro,
        "reintentos_registro": args.reintentos_registro,
        "workers_reintento": args.workers_reintento,
        "reintentos_extraccion": args.reintentos_extraccion,
        "espera_reintento_extraccion_segundos": args.espera_reintento_extraccion,
        "timeout_tabla_segundos": args.timeout_tabla,
        "intentos_anio_extraccion": args.intentos_anio_extraccion,
        "intentos_pagina_extraccion": args.intentos_pagina_extraccion,
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

    estado.actualizar(
        running=True,
        stage="inicializando",
        log=str(log_path),
        resumen=str(resumen_json),
        resumen_latest=str(resumen_latest),
        workers=args.workers,
        timeout_registro_segundos=args.timeout_registro,
    )

    # ──── Bloqueo compartido: evita que 2+ laptops corran el monitor a la vez ────
    # Cubre también a extraer_registros_documentos.py y main_procesar.py, que se
    # lanzan como subprocesos y heredan este mismo bloqueo automáticamente.
    lock = ProcesoLock(proceso="automatizar_registros_diario.py")
    try:
        estado.actualizar(stage="tomando_lock")
        lock.adquirir()

        def _salir_limpiamente(signum, frame):
            try:
                lock.liberar()
            finally:
                estado.finalizar(ok=False, mensaje=f"Proceso detenido por señal {signum}")
                raise SystemExit(128 + signum)

        try:
            signal.signal(signal.SIGTERM, _salir_limpiamente)
            signal.signal(signal.SIGINT, _salir_limpiamente)
        except Exception:
            pass

        estado.actualizar(stage="lock_adquirido")
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
        estado.finalizar(ok=True, mensaje=resumen["mensaje"], omitido_por_bloqueo=True)
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
            "--intentos-anio", str(args.intentos_anio_extraccion),
            "--intentos-pagina", str(args.intentos_pagina_extraccion),
            "--modo-anios", "todos",
        ]
        cmd_extraer.append("--headless" if headless else "--visible")
        try:
            registros_satys, intentos_extraccion = extraer_registros_satys_con_reintentos(
                cmd_extraer=cmd_extraer,
                output_path=registros_satys_hist,
                cwd=PROJECT_DIR,
                log_path=log_path,
                estado=estado,
                reintentos=args.reintentos_extraccion,
                espera_segundos=args.espera_reintento_extraccion,
            )
        except ExtraccionSatysAgotada as exc:
            resumen["intentos_extraccion_satys"] = exc.historial
            resumen["total_intentos_extraccion_satys"] = len(exc.historial)
            if exc.historial:
                resumen["return_code_extraer"] = exc.historial[-1]["return_code"]
            raise
        resumen["intentos_extraccion_satys"] = intentos_extraccion
        resumen["total_intentos_extraccion_satys"] = len(intentos_extraccion)
        resumen["return_code_extraer"] = intentos_extraccion[-1]["return_code"]
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
        vacio_confirmado_satys = bool(
            isinstance(resumen.get("extraccion_satys"), dict)
            and resumen["extraccion_satys"].get("estado") == "COMPLETO"
            and resumen["extraccion_satys"].get("integridad") == "VALIDADA"
            and resumen["extraccion_satys"].get("vacio_confirmado")
        )
        resumen["vacio_confirmado_satys"] = vacio_confirmado_satys
        if not registros_satys and not vacio_confirmado_satys:
            raise RuntimeError(
                "El TXT quedó vacío sin una confirmación auditable de SATyS. "
                "Se trata como error indeterminado, no como cero registros."
            )

        # 2) Leer evidencia Excel, columna 1711.
        estado.actualizar(stage="leyendo_excel_control", excel=str(args.excel))
        procesados_excel, excel_info = cargar_registros_procesados_excel(
            args.excel, args.sheet, args.header_registro
        )
        resumen["excel_info"] = excel_info

        estado.actualizar(
            stage="comparando_registros",
            total_registros_satys=len(registros_satys),
            total_procesados_excel=len(procesados_excel),
        )
        # 3) Comparar y guardar nuevos.
        nuevos_excel = [registro for registro in registros_satys if registro not in procesados_excel]

        # 3b) También incluir registros que ya están en el Excel pero tienen
        #     carpeta sin archivos reales (solo los JSONs generados por el programa).
        #     Esto garantiza que los re-intentos ocurran en cada corrida diaria.
        DESCARGA_BASE_DIARIO = ruta_configurada("descargas", "descargas")

        # Registros en Excel pero sin una descarga real válida. La decisión usa
        # la misma regla central que Parte 1 y main_procesar.py: los JSON de
        # metadata, archivos vacíos, temporales y auxiliares nunca cuentan.
        incompletos_en_excel = [
            reg for reg in registros_satys
            if reg in procesados_excel
            and not registro_esta_completo(DESCARGA_BASE_DIARIO, reg)
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
        estado.actualizar(
            stage="comparacion_lista",
            total_registros_satys=len(registros_satys),
            total_procesados_excel=len(procesados_excel),
            total_nuevos=len(nuevos),
            total_nuevos_excel=len(nuevos_excel),
            total_incompletos_reintento=len(incompletos_en_excel),
            registros_nuevos_preview=nuevos[:30],
        )

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
            rc_reconciliacion = 0
            if not args.sin_reconciliacion_global:
                rc_reconciliacion = ejecutar_reconciliacion_global(
                    python_exe=args.python_exe,
                    script=args.reconciliar_script,
                    excel=args.excel,
                    log_path=log_path,
                    estado=estado,
                )
            resumen["return_code_reconciliacion_global"] = rc_reconciliacion
            resumen["ok"] = rc_reconciliacion == 0
            if vacio_confirmado_satys:
                base_mensaje = "SATyS confirmó cero registros."
                mensaje_notificacion = "SATyS confirmó cero registros en todos los años consultados."
            else:
                base_mensaje = "No hay registros nuevos."
                mensaje_notificacion = (
                    f"Se revisaron {len(registros_satys)} registros; "
                    "todos existen en TrámitesCRT.xlsx."
                )
            resumen["mensaje"] = (
                f"{base_mensaje} Reconciliación global de TrámitesCRT.xlsx: código {rc_reconciliacion}."
            )
            notificar_windows(
                "SATyS CRT — sin registros nuevos",
                mensaje_notificacion,
                habilitado=not args.sin_notificacion,
            )
            estado.finalizar(
                ok=rc_reconciliacion == 0,
                mensaje=resumen["mensaje"],
                total_registros_satys=len(registros_satys),
                total_nuevos=0,
                return_code_reconciliacion_global=rc_reconciliacion,
            )
            sincronizar_estado_diario_depi()
            return rc_reconciliacion

        # 4) Ejecutar main_procesar.py por Registro.
        if args.no_procesar:
            rc_reconciliacion = 0
            if not args.sin_reconciliacion_global:
                rc_reconciliacion = ejecutar_reconciliacion_global(
                    python_exe=args.python_exe,
                    script=args.reconciliar_script,
                    excel=args.excel,
                    log_path=log_path,
                    estado=estado,
                )
            resumen["return_code_reconciliacion_global"] = rc_reconciliacion
            resumen["ok"] = rc_reconciliacion == 0
            resumen["mensaje"] = (
                "Se generó TXT de nuevos registros, pero no se procesó por --no-procesar. "
                f"Reconciliación global: código {rc_reconciliacion}."
            )
            notificar_windows(
                "SATyS CRT — registros nuevos detectados",
                f"{len(nuevos)} registro(s) nuevo(s). TXT: {args.registros_latest.name}",
                habilitado=not args.sin_notificacion,
            )
            estado.finalizar(
                ok=rc_reconciliacion == 0,
                mensaje=resumen["mensaje"],
                total_nuevos=len(nuevos),
                no_procesar=True,
                return_code_reconciliacion_global=rc_reconciliacion,
            )
            sincronizar_estado_diario_depi()
            return rc_reconciliacion

        cmd_main = [
            str(args.python_exe),
            str(args.main_script),
            "--archivo-registro", str(args.registros_latest),
            "--workers", str(args.workers),
            "--timeout-registro", str(args.timeout_registro),
            "--reintentos-registro", str(args.reintentos_registro),
            "--workers-reintento", str(args.workers_reintento),
            "--sin-lock",
        ]
        if headless:
            cmd_main.append("--headless")

        estado.actualizar(stage="procesando_registros_nuevos", total_nuevos=len(nuevos))
        rc_main = ejecutar_comando(
            cmd_main, PROJECT_DIR, log_path, "2) PROCESAR REGISTROS NUEVOS",
            estado=estado, etapa="procesando_registros_nuevos"
        )
        resumen["return_code_main"] = rc_main

        rc_reconciliacion = 0
        if not args.sin_reconciliacion_global:
            rc_reconciliacion = ejecutar_reconciliacion_global(
                python_exe=args.python_exe,
                script=args.reconciliar_script,
                excel=args.excel,
                log_path=log_path,
                estado=estado,
                sin_backup=(rc_main == 0),
            )
        resumen["return_code_reconciliacion_global"] = rc_reconciliacion
        rc_final = rc_main if rc_main != 0 else rc_reconciliacion

        fallidos_latest = PROJECT_DIR / "registros_fallidos" / "registros_fallidos_latest.txt"
        fallidos = leer_registros_txt(fallidos_latest) if fallidos_latest.exists() else []
        resumen["registros_fallidos_controlados"] = fallidos
        resumen["total_fallidos_controlados"] = len(fallidos)
        resumen["ok"] = rc_final == 0
        resumen["mensaje"] = (
            f"Procesados {len(nuevos)} registro(s) nuevo(s). Código main_procesar.py: {rc_main}. "
            f"Reconciliación global: {rc_reconciliacion}. Fallidos controlados: {len(fallidos)}."
        )

        notificar_windows(
            "SATyS CRT — proceso diario finalizado",
            f"Nuevos: {len(nuevos)} | Fallidos controlados: {len(fallidos)} | main_procesar.py código: {rc_main} | Log: {log_path.name}",
            habilitado=not args.sin_notificacion,
        )

        # ── Notificación por correo electrónico ──────────────────────────────
        # main_procesar.py ya envía el correo final con resultados correctos y
        # rutas de salida (Folios_Datos_Completos.xlsx, output/, descargas/ y
        # TrámitesCRT.xlsx). No enviamos un segundo correo aquí para evitar duplicados.
        if args.sin_email:
            print("\nℹ️  Correo deshabilitado por --sin-email.")
        else:
            print("\nℹ️  La notificación de resultados la envía main_procesar.py al finalizar.")

        estado.finalizar(
            ok=rc_final == 0,
            mensaje=resumen["mensaje"],
            total_nuevos=len(nuevos),
            total_fallidos_controlados=len(fallidos),
            return_code_main=rc_main,
            return_code_reconciliacion_global=rc_reconciliacion,
        )
        # ─────────────────────────────────────────────────────────────────────
        sincronizar_estado_diario_depi()

        return rc_final

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
        if _EMAIL_DISPONIBLE and not args.sin_email:
            _email_mod.enviar_notificacion(
                total_registros=resumen.get("total_nuevos", 0),
                exitosos=0,
                sin_operador=0,
                errores=resumen.get("total_nuevos", 0),
                registros=[],
                fecha_ejecucion=resumen.get("fecha_ejecucion"),
            )
        estado.finalizar(ok=False, mensaje=str(exc), errores=resumen.get("errores", []), traceback=resumen.get("traceback", ""))
        return 1

    finally:
        try:
            lock.liberar()
            print("🔓 Lock compartido liberado al finalizar automatizar_registros_diario.py.")
        except Exception:
            pass
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
