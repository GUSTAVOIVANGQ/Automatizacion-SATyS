#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Repara registros cuyo metadata_satys.json no contiene id_solicitante.

Características:
- Escanea descargas/ de forma recursiva.
- Reconsulta SATyS SIN volver a descargar documentos asociados.
- Guarda un checkpoint atómico después de cada registro/intento.
- Si hay apagón o cierre abrupto, la siguiente ejecución reanuda el registro
  que quedó pendiente y continúa con el resto de la cola.
- Al finalizar puede reconstruir Excel/output mediante main_procesar.py,
  aprovechando las descargas existentes (no vuelve a descargarlas).
"""

from __future__ import annotations

import argparse
import json
import os
import re
import signal
import subprocess
import sys
import tempfile
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

from configuracion_local import ruta_configurada
from proceso_lock import LockOcupadoError, ProcesoLock

PROJECT_DIR = Path(__file__).resolve().parent
DESCARGAS_DEFAULT = ruta_configurada("descargas", "descargas")
LOGS_DIR = PROJECT_DIR / "logs"
RUNS_DIR = PROJECT_DIR / "runs" / "reparacion_id_solicitante"
STATE_DEFAULT = LOGS_DIR / "reparacion_id_estado.json"
HISTORY_DEFAULT = LOGS_DIR / "reparacion_id_historial.json"
REGISTRO_RE = re.compile(r"\b[A-Z]{2,6}\d{2}-\d{3,}\b", re.IGNORECASE)

_STOP_REQUESTED = False
_ACTIVE_CHILD: subprocess.Popen[str] | None = None


def ahora_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def normalizar_registro(value: object) -> str:
    text = re.sub(r"\s+", "", str(value or "").strip().upper())
    match = REGISTRO_RE.search(text)
    return match.group(0).upper() if match else ""


def valor_vacio(value: object) -> bool:
    if value is None:
        return True
    if isinstance(value, str):
        return value.strip().lower() in {"", "null", "none", "nan", "n/a", "na"}
    return False


def leer_json(path: Path, default: Any = None) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def escribir_json_atomico(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(f".{path.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp")
    temp.write_text(
        json.dumps(data, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )
    os.replace(temp, path)


def append_historial(path: Path, item: dict[str, Any]) -> None:
    history = leer_json(path, [])
    if not isinstance(history, list):
        history = []
    history.append(item)
    escribir_json_atomico(path, history[-200:])


def registro_desde_metadata(path: Path, data: dict[str, Any]) -> str:
    for key in ("registro", "numero_registro", "1711"):
        registro = normalizar_registro(data.get(key))
        if registro:
            return registro
    for parent in (path.parent, *path.parents):
        registro = normalizar_registro(parent.name)
        if registro:
            return registro
        if parent == PROJECT_DIR:
            break
    return ""


def escanear_faltantes(descargas_dir: Path) -> tuple[list[str], dict[str, list[str]], list[dict[str, str]]]:
    """Devuelve cola única, rutas por registro y JSON inválidos/sin registro."""
    rutas: dict[str, list[str]] = {}
    errores: list[dict[str, str]] = []
    if not descargas_dir.exists():
        return [], {}, [{"path": str(descargas_dir), "error": "No existe el directorio de descargas"}]

    for path in sorted(descargas_dir.rglob("metadata_satys.json")):
        data = leer_json(path)
        if not isinstance(data, dict):
            errores.append({"path": str(path), "error": "JSON inválido o no es objeto"})
            continue
        registro = registro_desde_metadata(path, data)
        if not registro:
            errores.append({"path": str(path), "error": "No se pudo determinar el Registro CRT"})
            continue
        if valor_vacio(data.get("id_solicitante")):
            rutas.setdefault(registro, []).append(str(path))

    return sorted(rutas), rutas, errores


def rutas_metadata_registro(descargas_dir: Path, registro: str) -> list[Path]:
    encontrados: list[Path] = []
    for path in descargas_dir.rglob("metadata_satys.json"):
        data = leer_json(path)
        if isinstance(data, dict) and registro_desde_metadata(path, data) == registro:
            encontrados.append(path)
    return encontrados


def estado_id_registro(descargas_dir: Path, registro: str) -> dict[str, Any]:
    paths = rutas_metadata_registro(descargas_dir, registro)
    detalle: list[dict[str, Any]] = []
    for path in paths:
        data = leer_json(path)
        value = data.get("id_solicitante") if isinstance(data, dict) else None
        detalle.append({
            "path": str(path),
            "id_solicitante": value,
            "resuelto": not valor_vacio(value),
        })
    # Si un Registro tiene varios JSON, todos deben quedar resueltos para evitar
    # declarar éxito ocultando una carpeta secundaria incompleta.
    resolved = bool(detalle) and all(item["resuelto"] for item in detalle)
    return {"registro": registro, "resolved": resolved, "metadata": detalle}


def resumen_estado(state: dict[str, Any]) -> dict[str, int]:
    queue = list(state.get("queue") or [])
    resolved = list(state.get("resolved") or [])
    unresolved = state.get("unresolved") or {}
    completed = set(state.get("completed") or [])
    return {
        "detected": len(queue),
        "processed": len(completed),
        "resolved": len(resolved),
        "unresolved": len(unresolved),
        "pending": max(0, len(queue) - len(completed)),
    }


def actualizar_estado(path: Path, state: dict[str, Any], **changes: Any) -> None:
    state.update(changes)
    state["updated_at"] = ahora_iso()
    state["summary"] = resumen_estado(state)
    escribir_json_atomico(path, state)


def crear_estado_nuevo(
    state_path: Path,
    descargas_dir: Path,
    queue: list[str],
    metadata_paths: dict[str, list[str]],
    scan_errors: list[dict[str, str]],
    max_attempts: int,
    actualizar_salidas: bool,
) -> dict[str, Any]:
    state: dict[str, Any] = {
        "version": 1,
        "run_id": f"repair_{datetime.now().strftime('%Y%m%d_%H%M%S')}",
        "status": "pending",
        "running": False,
        "ok": None,
        "pid": os.getpid(),
        "created_at": ahora_iso(),
        "updated_at": ahora_iso(),
        "started_at": None,
        "finished_at": None,
        "descargas_dir": str(descargas_dir),
        "queue": queue,
        "metadata_paths_initial": metadata_paths,
        "scan_errors": scan_errors,
        "completed": [],
        "resolved": [],
        "unresolved": {},
        "attempts": {},
        "current": None,
        "next_index": 0,
        "max_attempts": max_attempts,
        "outputs_requested": actualizar_salidas,
        "outputs_updated": False,
        "outputs_return_code": None,
        "mensaje": f"Se detectaron {len(queue)} registro(s) sin id_solicitante.",
    }
    state["summary"] = resumen_estado(state)
    escribir_json_atomico(state_path, state)
    return state


def cargar_o_crear_estado(args: argparse.Namespace) -> dict[str, Any]:
    previous = leer_json(args.estado, {})
    can_resume = (
        isinstance(previous, dict)
        and previous.get("queue") is not None
        and previous.get("status") not in {"completed", "completed_with_warnings"}
        and not args.reiniciar_cola
    )
    if can_resume:
        previous["pid"] = os.getpid()
        previous["max_attempts"] = args.reintentos + 1
        previous["outputs_requested"] = not args.no_actualizar_salidas
        previous["mensaje"] = "Reanudando desde el último checkpoint persistido."
        actualizar_estado(args.estado, previous, status="resuming", running=False)
        return previous

    queue, paths, errors = escanear_faltantes(args.descargas)
    return crear_estado_nuevo(
        args.estado,
        args.descargas,
        queue,
        paths,
        errors,
        args.reintentos + 1,
        not args.no_actualizar_salidas,
    )


def ejecutar_con_salida(cmd: list[str], cwd: Path, env: dict[str, str]) -> int:
    global _ACTIVE_CHILD
    print("CMD:", " ".join(cmd), flush=True)
    proc = subprocess.Popen(
        cmd,
        cwd=str(cwd),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        bufsize=1,
        start_new_session=True,
    )
    _ACTIVE_CHILD = proc
    try:
        assert proc.stdout is not None
        for line in proc.stdout:
            print(line, end="", flush=True)
            if _STOP_REQUESTED and proc.poll() is None:
                try:
                    os.killpg(proc.pid, signal.SIGTERM)
                except Exception:
                    proc.terminate()
        return int(proc.wait())
    finally:
        _ACTIVE_CHILD = None


def comando_reparacion(args: argparse.Namespace, registro: str) -> list[str]:
    cmd = [
        args.python,
        str(PROJECT_DIR / "Parte1_descarga.py"),
        "--modo-registro",
        "--registros",
        registro,
        "--workers",
        "1",
        "--timeout-registro",
        str(args.timeout_registro),
        "--reintentos-registro",
        "0",
        "--workers-reintento",
        "1",
        "--forzar-registros",
        "--headless" if args.headless else "--visible",
    ]
    if not args.redescargar_archivos:
        cmd.append("--solo-metadatos-id")
    return cmd


def actualizar_salidas_finales(args: argparse.Namespace, state: dict[str, Any]) -> int:
    registros = list(state.get("queue") or [])
    if not registros:
        return 0
    RUNS_DIR.mkdir(parents=True, exist_ok=True)
    input_path = RUNS_DIR / f"{state['run_id']}_registros.txt"
    input_path.write_text("\n".join(registros) + "\n", encoding="utf-8")
    cmd = [
        args.python,
        str(PROJECT_DIR / "main_procesar.py"),
        "--archivo-registro",
        str(input_path),
        "--workers",
        "1",
        "--timeout-registro",
        str(args.timeout_registro),
        "--reintentos-registro",
        "0",
        "--workers-reintento",
        "1",
        "--sin-lock",
        "--headless" if args.headless else "--visible",
    ]
    print("\n[FINAL] Actualizando RPC, TrámitesCRT.xlsx y output/ sin volver a descargar archivos...", flush=True)
    env = os.environ.copy()
    env.setdefault("PYTHONUNBUFFERED", "1")
    env.setdefault("PYTHONIOENCODING", "utf-8")
    return ejecutar_con_salida(cmd, PROJECT_DIR, env)


def instalar_manejadores(state_path: Path, state: dict[str, Any]) -> None:
    def handler(signum: int, _frame: Any) -> None:
        global _STOP_REQUESTED
        _STOP_REQUESTED = True
        actualizar_estado(
            state_path,
            state,
            status="stopping",
            running=True,
            mensaje=f"Se recibió señal {signum}; se guardó el checkpoint y se detendrá al cerrar el proceso actual.",
        )
        child = _ACTIVE_CHILD
        if child and child.poll() is None:
            try:
                os.killpg(child.pid, signal.SIGTERM)
            except Exception:
                try:
                    child.terminate()
                except Exception:
                    pass

    signal.signal(signal.SIGTERM, handler)
    signal.signal(signal.SIGINT, handler)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Repara id_solicitante vacíos con checkpoint y reanudación por Registro."
    )
    parser.add_argument("--descargas", type=Path, default=DESCARGAS_DEFAULT)
    parser.add_argument("--estado", type=Path, default=STATE_DEFAULT)
    parser.add_argument("--historial", type=Path, default=HISTORY_DEFAULT)
    parser.add_argument("--python", default=os.getenv("SATYS_PYTHON", sys.executable))
    parser.add_argument("--reintentos", type=int, default=2, help="Reintentos adicionales por Registro (default: 2).")
    parser.add_argument("--timeout-registro", type=int, default=int(os.getenv("SATYS_TIMEOUT_REGISTRO", "900")))
    parser.add_argument("--headless", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--reiniciar-cola", action="store_true", help="Descarta el checkpoint anterior y vuelve a escanear.")
    parser.add_argument("--solo-analizar", action="store_true", help="Solo crea/muestra la cola; no abre SATyS.")
    parser.add_argument("--no-actualizar-salidas", action="store_true", help="No ejecuta Partes 3-4 al terminar.")
    parser.add_argument(
        "--redescargar-archivos",
        action="store_true",
        help="Además de reconsultar metadata, vuelve a descargar documentos asociados. Puede crear duplicados si SATyS conserva los mismos nombres.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    args.reintentos = max(0, min(args.reintentos, 10))
    args.timeout_registro = max(60, args.timeout_registro)
    args.descargas = args.descargas.resolve()
    args.estado = args.estado.resolve()
    args.historial = args.historial.resolve()

    state = cargar_o_crear_estado(args)
    state["redescargar_archivos"] = bool(args.redescargar_archivos)
    escribir_json_atomico(args.estado, state)
    instalar_manejadores(args.estado, state)

    summary = resumen_estado(state)
    print("=" * 78)
    print("SATyS — REPARACIÓN MANUAL DE id_solicitante")
    print(f"Descargas: {args.descargas}")
    print(f"Checkpoint: {args.estado}")
    print(f"Detectados: {summary['detected']} | Procesados: {summary['processed']} | Pendientes: {summary['pending']}")
    print("=" * 78, flush=True)

    if args.solo_analizar:
        actualizar_estado(
            args.estado,
            state,
            status="analyzed",
            running=False,
            ok=True,
            mensaje=f"Análisis terminado: {summary['detected']} registro(s) sin id_solicitante.",
        )
        print(json.dumps(state["summary"], ensure_ascii=False, indent=2))
        return 0

    if not state.get("queue"):
        actualizar_estado(
            args.estado,
            state,
            status="completed",
            running=False,
            ok=True,
            finished_at=ahora_iso(),
            mensaje="No se encontraron registros con id_solicitante vacío o null.",
        )
        append_historial(args.historial, dict(state))
        print("No hay registros por reparar.")
        return 0

    lock = ProcesoLock(proceso="reparar_id_solicitante.py")
    try:
        lock.adquirir()
    except LockOcupadoError as exc:
        actualizar_estado(
            args.estado,
            state,
            status="blocked",
            running=False,
            ok=False,
            mensaje=str(exc),
        )
        print(f"ERROR: {exc}", file=sys.stderr)
        return 3

    env = os.environ.copy()
    env.setdefault("PYTHONUNBUFFERED", "1")
    env.setdefault("PYTHONIOENCODING", "utf-8")
    completed = set(state.get("completed") or [])
    resolved = set(state.get("resolved") or [])
    unresolved: dict[str, Any] = dict(state.get("unresolved") or {})
    attempts: dict[str, int] = {str(k): int(v) for k, v in dict(state.get("attempts") or {}).items()}

    actualizar_estado(
        args.estado,
        state,
        status="running",
        running=True,
        ok=None,
        pid=os.getpid(),
        started_at=state.get("started_at") or ahora_iso(),
        finished_at=None,
        mensaje="Reparación en ejecución.",
    )

    try:
        queue = list(state.get("queue") or [])
        for index, registro in enumerate(queue):
            if _STOP_REQUESTED:
                break
            if registro in completed:
                continue

            max_attempts = int(state.get("max_attempts") or (args.reintentos + 1))
            success = False
            last_rc: int | None = None
            last_check: dict[str, Any] = {}

            while attempts.get(registro, 0) < max_attempts and not success and not _STOP_REQUESTED:
                attempts[registro] = attempts.get(registro, 0) + 1
                attempt = attempts[registro]
                actualizar_estado(
                    args.estado,
                    state,
                    current=registro,
                    next_index=index,
                    attempts=attempts,
                    status="running",
                    running=True,
                    mensaje=f"Procesando {registro}, intento {attempt}/{max_attempts}.",
                )
                print(f"\n[{index + 1}/{len(queue)}] {registro} — intento {attempt}/{max_attempts}", flush=True)
                last_rc = ejecutar_con_salida(comando_reparacion(args, registro), PROJECT_DIR, env)
                last_check = estado_id_registro(args.descargas, registro)
                success = bool(last_check.get("resolved"))
                print(
                    f"[VALIDACIÓN] {registro}: id_solicitante "
                    f"{'RESUELTO' if success else 'SIGUE VACÍO'}; return_code={last_rc}",
                    flush=True,
                )
                if not success and attempts[registro] < max_attempts and not _STOP_REQUESTED:
                    time.sleep(3)

            if _STOP_REQUESTED:
                break

            completed.add(registro)
            if success:
                resolved.add(registro)
                unresolved.pop(registro, None)
            else:
                unresolved[registro] = {
                    "attempts": attempts.get(registro, 0),
                    "return_code": last_rc,
                    "checked_at": ahora_iso(),
                    "detail": last_check,
                    "reason": "SATyS no mostró id_solicitante después de los intentos configurados; puede ser un trámite migrado sin ID en el DOM.",
                }

            actualizar_estado(
                args.estado,
                state,
                completed=sorted(completed, key=queue.index),
                resolved=sorted(resolved, key=queue.index),
                unresolved=unresolved,
                attempts=attempts,
                current=None,
                next_index=index + 1,
                mensaje=(
                    f"{registro} reparado correctamente."
                    if success
                    else f"{registro} sigue sin id_solicitante; se registró como no resuelto."
                ),
            )

        if _STOP_REQUESTED:
            actualizar_estado(
                args.estado,
                state,
                status="interrupted",
                running=False,
                ok=False,
                current=state.get("current"),
                mensaje="Ejecución interrumpida. La próxima corrida reanudará desde el checkpoint.",
            )
            return 130

        outputs_rc = state.get("outputs_return_code")
        if not args.no_actualizar_salidas and not state.get("outputs_updated"):
            actualizar_estado(
                args.estado,
                state,
                status="updating_outputs",
                running=True,
                current=None,
                mensaje="Actualizando RPC, TrámitesCRT.xlsx y output/.",
            )
            outputs_rc = actualizar_salidas_finales(args, state)
            actualizar_estado(
                args.estado,
                state,
                outputs_return_code=outputs_rc,
                outputs_updated=(outputs_rc == 0),
            )

        unresolved_count = len(unresolved)
        outputs_ok = args.no_actualizar_salidas or state.get("outputs_updated") is True
        final_ok = unresolved_count == 0 and outputs_ok
        final_status = "completed" if final_ok else "completed_with_warnings"
        mensaje = (
            "Reparación finalizada: todos los id_solicitante fueron recuperados."
            if unresolved_count == 0
            else f"Reparación finalizada con {unresolved_count} registro(s) aún sin id_solicitante."
        )
        if not outputs_ok:
            mensaje += f" La actualización final de salidas terminó con código {outputs_rc}."

        actualizar_estado(
            args.estado,
            state,
            status=final_status,
            running=False,
            ok=final_ok,
            current=None,
            finished_at=ahora_iso(),
            mensaje=mensaje,
        )
        append_historial(args.historial, dict(state))
        print("\n" + mensaje)
        print(json.dumps(state["summary"], ensure_ascii=False, indent=2))
        return 0 if outputs_ok else 2
    except Exception as exc:
        actualizar_estado(
            args.estado,
            state,
            status="failed",
            running=False,
            ok=False,
            finished_at=ahora_iso(),
            error=str(exc),
            mensaje=f"Error no controlado: {exc}. La próxima corrida reanudará desde el checkpoint.",
        )
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    finally:
        lock.liberar()


if __name__ == "__main__":
    raise SystemExit(main())
