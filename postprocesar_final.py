#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Postproceso final SATyS sin volver a consultar el portal.

Orden productivo:
  1) Completar Solicitante/Representante desde todos los PDF de descargas.
  2) Reconciliar TrámitesCRT.xlsx desde metadata, sin recopiar todo output.
  3) Reparar _sin_operador exclusivamente con RPC público y clasificar correos.
  4) Sincronizar output/ + TrámitesCRT.xlsx al recurso DEPI.
  5) Enviar el correo consolidado con EN REVISIÓN calculado desde el Excel final.

La fuente original ``descargas`` nunca se elimina ni se mueve.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

from configuracion_local import carpeta_compartida, ruta_configurada
from proceso_lock import LockOcupadoError, ProcesoLock
from sincronizacion_depi import sincronizar_salidas

PROJECT_DIR = Path(__file__).resolve().parent
LOGS_DIR = PROJECT_DIR / "logs"
PYTHON = Path(os.getenv("SATYS_PYTHON", sys.executable))
REMITENTES_TIMEOUT = max(300, int(os.getenv("SATYS_REMITENTES_PDF_TIMEOUT", "1800")))
RECON_TIMEOUT = max(300, int(os.getenv("SATYS_RECONCILIACION_GLOBAL_TIMEOUT", "1800")))
SINOP_TIMEOUT = max(300, int(os.getenv("SATYS_SIN_OPERADOR_RPC_PUBLICO_TIMEOUT", "1800")))


def _print_and_log(text: str, fh) -> None:
    if not text:
        return
    print(text, end="" if text.endswith("\n") else "\n")
    fh.write(text)
    if not text.endswith("\n"):
        fh.write("\n")
    fh.flush()


def ejecutar_paso(cmd: list[str], titulo: str, timeout: int, fh) -> int:
    separador = "=" * 90
    _print_and_log(f"\n{separador}\n{titulo}\nCMD: {' '.join(cmd)}\n{separador}\n", fh)
    inicio = time.monotonic()
    proc = subprocess.Popen(
        cmd,
        cwd=PROJECT_DIR,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    try:
        out, _ = proc.communicate(timeout=timeout)
        _print_and_log(out or "", fh)
        rc = int(proc.returncode or 0)
    except subprocess.TimeoutExpired as exc:
        parcial = exc.output or ""
        if isinstance(parcial, bytes):
            parcial = parcial.decode("utf-8", errors="replace")
        _print_and_log(parcial, fh)
        proc.terminate()
        try:
            resto, _ = proc.communicate(timeout=30)
        except subprocess.TimeoutExpired:
            proc.kill()
            resto, _ = proc.communicate()
        _print_and_log(resto or "", fh)
        rc = 124
        _print_and_log(f"[TIMEOUT] {titulo}: excedió {timeout}s; etapa terminada con rc=124.\n", fh)
    dur = time.monotonic() - inicio
    _print_and_log(f"[{titulo}] return_code={rc} duracion={dur:.1f}s\n", fh)
    return rc


def cargar_resultados_email(descargas: Path) -> list[dict]:
    import automatizar_registros_diario as diario

    resultados: list[dict] = []
    resultados.extend(diario.cargar_resultados_procesamiento(
        descargas / "internos" / "procesamiento_log_internos.json",
        origen="internos",
    ))
    resultados.extend(diario.cargar_resultados_procesamiento(
        descargas / "procesamiento_log_registros.json",
        origen="oficialia",
    ))
    reparacion = diario.cargar_reparaciones_sin_operador_rpc_publico(
        LOGS_DIR / "reparacion_sin_operador_rpc_publico_ultimo.json"
    )
    return diario.aplicar_reparaciones_a_resultados_email(resultados, reparacion)


def construir_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Ejecuta sólo el postproceso final: Excel/PDF -> reconciliación -> RPC público -> output/DEPI -> correo."
    )
    p.add_argument("--excel", type=Path, default=ruta_configurada("excel", "TrámitesCRT.xlsx"))
    p.add_argument("--descargas", type=Path, default=ruta_configurada("descargas", "descargas"))
    p.add_argument("--output", type=Path, default=ruta_configurada("output", "output"))
    p.add_argument("--shared", type=Path, default=carpeta_compartida())
    p.add_argument("--sin-email", action="store_true", help="Ejecuta todo el postproceso pero no envía correo final.")
    p.add_argument("--sin-sync-depi", action="store_true", help="No hace la sincronización final output+Excel a DEPI.")
    p.add_argument("--sin-lock", action="store_true", help="Sólo para pruebas; no usar en producción.")
    return p


def main() -> int:
    args = construir_parser().parse_args()
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    sello = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_path = LOGS_DIR / f"postproceso_final_{sello}.log"
    resumen_path = LOGS_DIR / f"postproceso_final_{sello}.json"
    latest_path = LOGS_DIR / "postproceso_final_ultimo.json"

    lock = None
    resumen: dict = {
        "fecha_inicio": datetime.now().isoformat(),
        "excel": str(args.excel),
        "descargas": str(args.descargas),
        "output": str(args.output),
        "shared": str(args.shared),
        "pasos": {},
    }
    try:
        if not args.sin_lock:
            lock = ProcesoLock(proceso="postprocesar_final.py")
            lock.adquirir()
            print("🔒 Lock global SATyS adquirido para postproceso final.")

        if not args.excel.is_file():
            raise FileNotFoundError(f"No existe Excel: {args.excel}")
        if not args.descargas.is_dir():
            raise FileNotFoundError(f"No existe descargas/: {args.descargas}")
        args.output.mkdir(parents=True, exist_ok=True)

        with log_path.open("a", encoding="utf-8") as fh:
            rc_rem = ejecutar_paso([
                str(PYTHON), str(PROJECT_DIR / "completar_remitentes_desde_pdfs.py"),
                "--excel", str(args.excel),
                "--descargas", str(args.descargas),
                "--logs-dir", str(LOGS_DIR),
                "--sin-lock",
            ], "1) COMPLETAR SOLICITANTE/REPRESENTANTE DESDE PDF", REMITENTES_TIMEOUT, fh)
            resumen["pasos"]["remitentes_pdf"] = rc_rem

            rc_rec = ejecutar_paso([
                str(PYTHON), str(PROJECT_DIR / "reconciliar_metadata_global.py"),
                "--excel", str(args.excel),
                "--descargas", str(args.descargas),
                "--output", str(args.output),
                "--resumen-json", str(LOGS_DIR / "reconciliacion_global_ultimo.json"),
                "--sin-reorganizar-output",
            ], "2) RECONCILIAR TRÁMITESCRT DESDE METADATA", RECON_TIMEOUT, fh)
            resumen["pasos"]["reconciliacion_global"] = rc_rec

            cmd_rpc = [
                str(PYTHON), str(PROJECT_DIR / "resolver_sin_operador_rpc_publico.py"),
                "--excel", str(args.excel),
                "--descargas", str(args.descargas),
                "--output", str(args.output),
                "--shared", str(args.shared),
                "--logs-dir", str(LOGS_DIR),
                "--sin-lock",
            ]
            if args.sin_sync_depi:
                cmd_rpc.append("--sin-sincronizar-depi")
            rc_rpc = ejecutar_paso(
                cmd_rpc,
                "3) REPARAR _SIN_OPERADOR RPC PÚBLICO + CLASIFICAR CORREOS",
                SINOP_TIMEOUT,
                fh,
            )
            resumen["pasos"]["sin_operador_rpc_publico"] = rc_rpc

            rc_sync = 0
            if args.sin_sync_depi:
                _print_and_log("\n4) Sincronización final DEPI omitida por --sin-sync-depi.\n", fh)
            else:
                _print_and_log("\n4) SINCRONIZAR OUTPUT + TrámitesCRT.xlsx A DEPI\n", fh)
                sync = sincronizar_salidas(
                    PROJECT_DIR,
                    args.shared,
                    directorios=("output",),
                    archivos=("TrámitesCRT.xlsx",),
                )
                resumen["sincronizacion_depi"] = {
                    "archivos_copiados": sync.archivos_copiados,
                    "directorios_creados": sync.directorios_creados,
                    "omitidos": sync.omitidos,
                    "json_output_eliminados": sync.json_output_eliminados,
                    "errores": list(sync.errores),
                }
                rc_sync = 1 if sync.errores else 0
                _print_and_log(
                    f"DEPI: {sync.archivos_copiados} archivo(s) copiado(s), "
                    f"{len(sync.errores)} error(es). Destino={args.shared}\n",
                    fh,
                )
                for err in sync.errores[:20]:
                    _print_and_log(f"  ERROR DEPI: {err}\n", fh)
            resumen["pasos"]["sincronizacion_depi"] = rc_sync

            # El correo se envía después de que el Excel final y output ya están
            # replicados en DEPI. Su tarjeta EN REVISIÓN se calcula directamente
            # desde este TrámitesCRT.xlsx final, excluyendo (correos).
            rc_email = 0
            if args.sin_email:
                _print_and_log("\n5) Correo omitido por --sin-email.\n", fh)
            else:
                import automatizar_registros_diario as diario
                resultados = cargar_resultados_email(args.descargas)
                errores_pasos = [k for k, v in resumen["pasos"].items() if v]
                error_general = ""
                if errores_pasos:
                    error_general = "Postproceso con etapa(s) no exitosa(s): " + ", ".join(errores_pasos)
                ok_email = diario.enviar_resumen_email_diario(
                    resultados=resultados,
                    log_path=log_path,
                    excel_path=args.excel,
                    modo="POSTPROCESO FINAL CONSOLIDADO",
                    error_general=error_general,
                )
                rc_email = 0 if ok_email else 1
                _print_and_log(f"\n5) CORREO FINAL return_code={rc_email}\n", fh)
            resumen["pasos"]["correo_final"] = rc_email

        import notificar_email
        resumen["conteo_excel_final"] = notificar_email.conteos_revision_desde_excel(args.excel)
        resumen["fecha_fin"] = datetime.now().isoformat()
        resumen["ok"] = all(v == 0 for v in resumen["pasos"].values())
        text = json.dumps(resumen, ensure_ascii=False, indent=2)
        resumen_path.write_text(text, encoding="utf-8")
        latest_path.write_text(text, encoding="utf-8")
        print("\n" + "=" * 90)
        print("POSTPROCESO FINAL TERMINADO")
        print(json.dumps(resumen["conteo_excel_final"], ensure_ascii=False, indent=2))
        print(f"Resumen: {resumen_path}")
        print("=" * 90)
        return 0 if resumen["ok"] else 2
    except LockOcupadoError as exc:
        print(f"ERROR: no se inicia postproceso final porque SATyS está ocupado: {exc}", file=sys.stderr)
        return 3
    except Exception as exc:
        resumen["fecha_fin"] = datetime.now().isoformat()
        resumen["ok"] = False
        resumen["error"] = f"{type(exc).__name__}: {exc}"
        text = json.dumps(resumen, ensure_ascii=False, indent=2)
        resumen_path.write_text(text, encoding="utf-8")
        latest_path.write_text(text, encoding="utf-8")
        print(f"ERROR fatal en postproceso final: {exc}", file=sys.stderr)
        return 1
    finally:
        if lock is not None:
            lock.liberar()


if __name__ == "__main__":
    raise SystemExit(main())
