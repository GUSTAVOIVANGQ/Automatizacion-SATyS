#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
satys_api.py
============
API + frontend ligero para SATyS CRT.

Objetivo:
- Mantener el proceso real fuera del frontend.
- systemd ejecuta el monitor diario a la 01:00 o a la hora configurada.
- El dashboard permite monitorear, descargar salidas y lanzar ejecuciones manuales controladas.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Any

from fastapi import Body, FastAPI, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, PlainTextResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

PROJECT_DIR = Path(__file__).resolve().parent
LOGS_DIR = PROJECT_DIR / "logs"
STATIC_DIR = PROJECT_DIR / "web" / "static"
TEMPLATES_DIR = PROJECT_DIR / "web" / "templates"
RUNS_DIR = PROJECT_DIR / "runs"
EXPORTS_DIR = PROJECT_DIR / "exports"
SYSTEMD_DIR = PROJECT_DIR / "systemd"
CONFIG_JSON = PROJECT_DIR / "config_satys_web.json"

ESTADO_JSON = Path(os.getenv("SATYS_ESTADO_JSON", str(LOGS_DIR / "estado_actual.json")))
RESUMEN_LATEST = LOGS_DIR / "monitor_registros_ultimo.json"
MANUAL_ESTADO_JSON = LOGS_DIR / "manual_estado_actual.json"
MANUAL_HISTORIAL_JSON = LOGS_DIR / "manual_historial.json"
REPAIR_ESTADO_JSON = LOGS_DIR / "reparacion_id_estado.json"
REPAIR_HISTORIAL_JSON = LOGS_DIR / "reparacion_id_historial.json"
SERVICE_NAME = os.getenv("SATYS_SYSTEMD_SERVICE", "satys-diario.service")
TIMER_NAME = os.getenv("SATYS_SYSTEMD_TIMER", "satys-diario.timer")
PROJECT_NAME = os.getenv("SATYS_PROJECT_NAME", "SATyS CRT")

EXCEL_CONTROL = PROJECT_DIR / "TrámitesCRT.xlsx"
EXCEL_CONSOLIDADO = PROJECT_DIR / "output" / "Folios_Datos_Completos.xlsx"
OUTPUT_DIR = PROJECT_DIR / "output"
DESCARGAS_DIR = PROJECT_DIR / "descargas"
REGISTROS_DIR = PROJECT_DIR / "registros_diarios"

app = FastAPI(title="SATyS CRT", version="1.3.0")

for folder in (LOGS_DIR, RUNS_DIR, EXPORTS_DIR):
    folder.mkdir(parents=True, exist_ok=True)

if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


def _now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _read_json(path: Path) -> Any:
    if not path.exists():
        raise HTTPException(status_code=404, detail=f"No existe: {path.name}")
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"No se pudo leer {path.name}: {exc}") from exc


def _read_json_default(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def _write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    tmp.replace(path)


def _run_command(args: list[str], timeout: int = 8) -> dict[str, Any]:
    try:
        r = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
        return {
            "ok": r.returncode == 0,
            "returncode": r.returncode,
            "stdout": r.stdout.strip(),
            "stderr": r.stderr.strip(),
            "cmd": args,
        }
    except Exception as exc:
        return {"ok": False, "returncode": None, "stdout": "", "stderr": str(exc), "cmd": args}


def _resolve_log_path_from_resumen(resumen: dict[str, Any]) -> Path | None:
    candidates: list[object] = []
    paths = resumen.get("paths")
    if isinstance(paths, dict):
        candidates.extend([paths.get("log"), paths.get("log_path")])
    candidates.extend([resumen.get("log"), resumen.get("log_path"), resumen.get("archivo_log")])

    for candidate in candidates:
        if not candidate:
            continue
        p = Path(str(candidate))
        if not p.is_absolute():
            p = PROJECT_DIR / p
        if p.exists() and p.is_file():
            return p
    return None


def _resolve_existing_path(value: object) -> Path | None:
    """Resuelve una ruta guardada en JSON solo si existe y es archivo."""
    if not value:
        return None
    p = Path(str(value))
    if not p.is_absolute():
        p = PROJECT_DIR / p
    try:
        if p.exists() and p.is_file():
            return p
    except Exception:
        return None
    return None


def _latest_daily_log_path() -> Path | None:
    """
    Devuelve el log diario que debe ver la UI.

    Importante: monitor_registros_ultimo.json se actualiza al FINAL de una corrida.
    Si se lanza una segunda corrida el mismo día, ese resumen puede seguir apuntando
    al log anterior mientras el nuevo proceso ya está escribiendo otro archivo.

    Por eso el orden correcto es:
      1) Si estado_actual.json dice running=True, usar el log vivo del estado.
      2) Si no hay estado vivo, usar el monitor_registros_*.log más reciente por mtime.
      3) Solo como fallback usar monitor_registros_ultimo.json.
    """
    # 1) Corrida viva: estado_actual.json es la fuente de verdad.
    try:
        if ESTADO_JSON.exists():
            estado = json.loads(ESTADO_JSON.read_text(encoding="utf-8"))
            # automatizar_registros_diario.py escribe la clave "log".
            for key in ("log", "log_actual", "log_path", "archivo_log"):
                p = _resolve_existing_path(estado.get(key))
                if p and estado.get("running") is True:
                    return p
    except Exception:
        pass

    # 2) Usar el archivo .log más reciente. Esto cubre reinicios o segundas
    # corridas donde el JSON de resumen aún apunta al intento anterior.
    try:
        logs = sorted(
            LOGS_DIR.glob("monitor_registros_*.log"),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
        if logs:
            return logs[0]
    except Exception:
        pass

    # 3) Fallback histórico.
    if RESUMEN_LATEST.exists():
        try:
            resumen = json.loads(RESUMEN_LATEST.read_text(encoding="utf-8"))
            p = _resolve_log_path_from_resumen(resumen)
            if p:
                return p
        except Exception:
            pass

    # 4) Último fallback desde estado, aunque no esté running.
    try:
        if ESTADO_JSON.exists():
            estado = json.loads(ESTADO_JSON.read_text(encoding="utf-8"))
            for key in ("log", "log_actual", "log_path", "archivo_log"):
                p = _resolve_existing_path(estado.get(key))
                if p:
                    return p
    except Exception:
        pass

    return None


def _latest_manual_log_path() -> Path | None:
    estado = _read_json_default(MANUAL_ESTADO_JSON, {})
    for key in ("log_path", "archivo_log"):
        value = estado.get(key)
        if value:
            p = Path(str(value))
            if not p.is_absolute():
                p = PROJECT_DIR / p
            if p.exists() and p.is_file():
                return p
    logs = sorted(LOGS_DIR.glob("manual_*.log"), key=lambda p: p.stat().st_mtime, reverse=True)
    return logs[0] if logs else None


def _latest_repair_log_path() -> Path | None:
    estado = _read_json_default(REPAIR_ESTADO_JSON, {})
    for key in ("log_path", "archivo_log"):
        value = estado.get(key)
        if value:
            p = Path(str(value))
            if not p.is_absolute():
                p = PROJECT_DIR / p
            if p.exists() and p.is_file():
                return p
    logs = sorted(LOGS_DIR.glob("reparacion_id_*.log"), key=lambda p: p.stat().st_mtime, reverse=True)
    return logs[0] if logs else None


def _tail_lines(path: Path, tail: int = 300) -> str:
    tail = max(1, min(int(tail), 5000))
    lineas = path.read_text(encoding="utf-8", errors="replace").splitlines()
    return "\n".join(lineas[-tail:]) + ("\n" if lineas else "")


def _is_pid_running(pid: int | None) -> bool:
    if not pid or pid <= 0:
        return False
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except Exception:
        return False


def _file_info(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"exists": False, "path": str(path)}
    stat = path.stat()
    return {
        "exists": True,
        "path": str(path),
        "name": path.name,
        "size": stat.st_size,
        "modified_at": datetime.fromtimestamp(stat.st_mtime).isoformat(timespec="seconds"),
    }


def _dir_info(path: Path) -> dict[str, Any]:
    info = _file_info(path)
    if not path.exists() or not path.is_dir():
        return info | {"files": 0, "dirs": 0}
    files = 0
    dirs = 0
    size = 0
    for item in path.rglob("*"):
        try:
            if item.is_dir():
                dirs += 1
            elif item.is_file():
                files += 1
                size += item.stat().st_size
        except Exception:
            pass
    info.update({"files": files, "dirs": dirs, "size": size})
    return info


def _service_status() -> dict[str, Any]:
    active = _run_command(["systemctl", "is-active", SERVICE_NAME])
    enabled = _run_command(["systemctl", "is-enabled", SERVICE_NAME])
    timer_active = _run_command(["systemctl", "is-active", TIMER_NAME])
    timer_enabled = _run_command(["systemctl", "is-enabled", TIMER_NAME])
    next_timer = _run_command(["systemctl", "list-timers", TIMER_NAME, "--no-pager", "--no-legend"])
    return {
        "service": SERVICE_NAME,
        "timer": TIMER_NAME,
        "service_active": active.get("stdout") or active.get("stderr"),
        "service_enabled": enabled.get("stdout") or enabled.get("stderr"),
        "timer_active": timer_active.get("stdout") or timer_active.get("stderr"),
        "timer_enabled": timer_enabled.get("stdout") or timer_enabled.get("stderr"),
        "next_timer_raw": next_timer.get("stdout"),
        "commands_ok": {
            "is_active": active["ok"],
            "is_enabled": enabled["ok"],
            "timer_active": timer_active["ok"],
            "timer_enabled": timer_enabled["ok"],
            "next_timer": next_timer["ok"],
        },
    }


def _get_config() -> dict[str, Any]:
    cfg = _read_json_default(CONFIG_JSON, {})
    cfg.setdefault("timer_hora", os.getenv("SATYS_TIMER_HORA", "01:00"))
    cfg.setdefault("workers", 6)
    cfg.setdefault("headless", True)
    return cfg


def _set_config(cfg: dict[str, Any]) -> None:
    _write_json(CONFIG_JSON, cfg)


def _timer_unit_text(hora: str) -> str:
    return f"""[Unit]
Description=Ejecuta SATyS CRT una vez al día a las {hora}

[Timer]
OnCalendar=*-*-* {hora}:00 America/Mexico_City
Persistent=false
AccuracySec=1min
RandomizedDelaySec=0
Unit=satys-diario.service

[Install]
WantedBy=timers.target
"""


def _install_timer_if_allowed(hora: str) -> dict[str, Any]:
    project_timer = SYSTEMD_DIR / "satys-diario.timer"
    SYSTEMD_DIR.mkdir(exist_ok=True)
    project_timer.write_text(_timer_unit_text(hora), encoding="utf-8")

    if os.getenv("SATYS_API_ALLOW_TIMER_EDIT", "0") != "1":
        return {
            "installed": False,
            "message": "Hora guardada en systemd/satys-diario.timer. Para aplicarla en el sistema, habilita SATYS_API_ALLOW_TIMER_EDIT=1 o copia el archivo manualmente con sudo.",
            "manual_commands": [
                "sudo cp systemd/satys-diario.timer /etc/systemd/system/satys-diario.timer",
                "sudo systemctl daemon-reload",
                "sudo systemctl enable --now satys-diario.timer",
                "systemctl list-timers | grep satys",
            ],
        }

    commands = [
        ["sudo", "-n", "cp", str(project_timer), "/etc/systemd/system/satys-diario.timer"],
        ["sudo", "-n", "systemctl", "daemon-reload"],
        ["sudo", "-n", "systemctl", "enable", "--now", TIMER_NAME],
        ["sudo", "-n", "systemctl", "restart", TIMER_NAME],
    ]
    results = [_run_command(cmd, timeout=20) for cmd in commands]
    ok = all(r["ok"] for r in results)
    return {
        "installed": ok,
        "message": "Timer actualizado en systemd." if ok else "Se guardó el archivo, pero no se pudo aplicar con sudo -n.",
        "results": results,
    }


def _append_manual_history(item: dict[str, Any]) -> None:
    hist = _read_json_default(MANUAL_HISTORIAL_JSON, [])
    if not isinstance(hist, list):
        hist = []
    hist.append(item)
    _write_json(MANUAL_HISTORIAL_JSON, hist[-200:])


def _refresh_manual_state() -> dict[str, Any]:
    estado = _read_json_default(MANUAL_ESTADO_JSON, {"running": False, "mensaje": "Aún no hay corrida manual"})
    if estado.get("running") and not _is_pid_running(int(estado.get("pid") or 0)):
        estado["running"] = False
        estado.setdefault("finished_at", _now_iso())
        estado.setdefault("return_code", "desconocido")
        estado["mensaje"] = "El proceso ya no está activo; revisa el log para confirmar el resultado."
        _write_json(MANUAL_ESTADO_JSON, estado)
    return estado


def _wait_manual_process(proc: subprocess.Popen, state_path: Path, history_item: dict[str, Any], log_file) -> None:
    try:
        rc = proc.wait()
        try:
            log_file.flush()
            log_file.close()
        except Exception:
            pass
        estado = _read_json_default(state_path, {})
        estado.update({
            "running": False,
            "finished_at": _now_iso(),
            "return_code": rc,
            "ok": rc == 0,
            "mensaje": f"Corrida manual finalizada con código {rc}.",
        })
        _write_json(state_path, estado)
        history_item.update({"finished_at": estado["finished_at"], "return_code": rc, "ok": rc == 0})
        _append_manual_history(history_item)
    except Exception as exc:
        estado = _read_json_default(state_path, {})
        estado.update({
            "running": False,
            "finished_at": _now_iso(),
            "ok": False,
            "error": str(exc),
            "mensaje": f"Error esperando proceso manual: {exc}",
        })
        _write_json(state_path, estado)
        history_item.update({"finished_at": estado["finished_at"], "return_code": None, "ok": False, "error": str(exc)})
        _append_manual_history(history_item)


def _zip_dir(source_dir: Path, prefix: str) -> Path:
    if not source_dir.exists() or not source_dir.is_dir():
        raise HTTPException(status_code=404, detail=f"No existe la carpeta: {source_dir.name}")
    EXPORTS_DIR.mkdir(exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    zip_path = EXPORTS_DIR / f"{prefix}_{ts}.zip"
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for item in source_dir.rglob("*"):
            if item.is_file():
                zf.write(item, arcname=str(Path(source_dir.name) / item.relative_to(source_dir)))
    return zip_path


REGISTRO_RE_API = re.compile(r"\b[A-Z]{2,8}\d{2}-\d{3,}\b", re.IGNORECASE)


def _refresh_repair_state() -> dict[str, Any]:
    estado = _read_json_default(
        REPAIR_ESTADO_JSON,
        {
            "running": False,
            "status": "not_started",
            "mensaje": "Aún no se ha ejecutado la reparación de id_solicitante.",
            "summary": {"detected": 0, "processed": 0, "resolved": 0, "unresolved": 0, "pending": 0},
        },
    )
    if estado.get("running") and not _is_pid_running(int(estado.get("pid") or 0)):
        estado["running"] = False
        estado["status"] = "interrupted"
        estado["ok"] = False
        estado.setdefault("finished_at", _now_iso())
        estado["mensaje"] = "El proceso ya no está activo. El checkpoint se conserva; usa Reanudar para continuar."
        _write_json(REPAIR_ESTADO_JSON, estado)
    return estado


def _wait_repair_process(proc: subprocess.Popen, log_file) -> None:
    try:
        rc = proc.wait()
        try:
            log_file.flush()
            log_file.close()
        except Exception:
            pass
        estado = _read_json_default(REPAIR_ESTADO_JSON, {})
        if estado.get("running"):
            estado.update({
                "running": False,
                "finished_at": _now_iso(),
                "return_code": rc,
                "ok": rc == 0,
                "status": "completed" if rc == 0 else "interrupted",
                "mensaje": "Reparación finalizada." if rc == 0 else "La reparación se detuvo; el checkpoint permite reanudarla.",
            })
            _write_json(REPAIR_ESTADO_JSON, estado)
    except Exception as exc:
        estado = _read_json_default(REPAIR_ESTADO_JSON, {})
        estado.update({
            "running": False,
            "finished_at": _now_iso(),
            "ok": False,
            "status": "failed",
            "error": str(exc),
            "mensaje": f"Error esperando el reparador: {exc}",
        })
        _write_json(REPAIR_ESTADO_JSON, estado)


def _normalizar_registro_api(valor: str) -> str:
    texto = str(valor or "").replace("\u00a0", " ").strip().upper()
    texto = re.sub(r"\s+", "", texto)
    m = REGISTRO_RE_API.search(texto)
    if not m:
        raise HTTPException(status_code=400, detail="Registro inválido. Usa formato como CRT26-027838.")
    return m.group(0).upper()


def _is_relative_to(child: Path, parent: Path) -> bool:
    try:
        child.resolve().relative_to(parent.resolve())
        return True
    except Exception:
        return False


def _root_label(path: Path) -> str:
    if _is_relative_to(path, DESCARGAS_DIR):
        return "descargas"
    if _is_relative_to(path, OUTPUT_DIR):
        return "output"
    return "proyecto"


def _path_info_for_registro(path: Path) -> dict[str, Any]:
    stat = path.stat()
    try:
        rel = path.resolve().relative_to(PROJECT_DIR.resolve())
    except Exception:
        rel = path
    return {
        "tipo": "carpeta" if path.is_dir() else "archivo",
        "raiz": _root_label(path),
        "path": str(path),
        "relpath": str(rel),
        "name": path.name,
        "size": stat.st_size if path.is_file() else None,
        "modified_at": datetime.fromtimestamp(stat.st_mtime).isoformat(timespec="seconds"),
    }


def _dedupe_candidate_paths(paths: list[Path]) -> list[Path]:
    unique: list[Path] = []
    seen: set[Path] = set()
    for p in sorted(paths, key=lambda x: (len(x.resolve().parts), str(x).lower())):
        try:
            rp = p.resolve()
        except Exception:
            continue
        if rp in seen:
            continue
        # Si ya se incluyó una carpeta padre, no duplicar hijos.
        if any(_is_relative_to(rp, parent) for parent in seen):
            continue
        seen.add(rp)
        unique.append(p)
    return unique


def _buscar_carpetas_registro(registro: str, tipo: str = "auto") -> list[Path]:
    """
    Búsqueda simple por registro único.
    - Busca nombres de carpetas/archivos que contengan el registro.
    - Busca metadata JSON que contenga el registro y toma la carpeta padre.
    No usa manifest ni modifica el flujo principal.
    """
    registro = _normalizar_registro_api(registro)
    tipo = (tipo or "auto").strip().lower()
    if tipo not in {"auto", "descargas", "output"}:
        raise HTTPException(status_code=400, detail="tipo debe ser auto, descargas u output.")

    roots: list[Path] = []
    if tipo in {"auto", "descargas"}:
        roots.append(DESCARGAS_DIR)
    if tipo in {"auto", "output"}:
        roots.append(OUTPUT_DIR)

    candidates: list[Path] = []
    reg_upper = registro.upper()

    for root in roots:
        if not root.exists():
            continue
        # Coincidencia en nombre de carpeta o archivo.
        for item in root.rglob("*"):
            try:
                if reg_upper in item.name.upper():
                    candidates.append(item if item.is_dir() else item.parent)
            except Exception:
                continue

        # Coincidencia dentro de JSON de metadata.
        for js in root.rglob("*.json"):
            try:
                if js.stat().st_size > 2_000_000:
                    continue
                content = js.read_text(encoding="utf-8", errors="ignore").upper()
                if reg_upper in content:
                    candidates.append(js.parent)
            except Exception:
                continue

    # Mantener solo carpetas/archivos dentro de roots permitidas.
    allowed = [r.resolve() for r in roots if r.exists()]
    safe: list[Path] = []
    for c in candidates:
        try:
            rc = c.resolve()
        except Exception:
            continue
        if any(_is_relative_to(rc, a) for a in allowed):
            safe.append(c)

    return _dedupe_candidate_paths(safe)


def _zip_paths(paths: list[Path], prefix: str) -> Path:
    if not paths:
        raise HTTPException(status_code=404, detail="No se encontraron carpetas para comprimir.")
    EXPORTS_DIR.mkdir(exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    zip_path = EXPORTS_DIR / f"{prefix}_{ts}.zip"
    written: set[str] = set()

    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for base in paths:
            if not base.exists():
                continue
            items = [base] if base.is_file() else [p for p in base.rglob("*") if p.is_file()]
            for item in items:
                try:
                    rel = item.resolve().relative_to(PROJECT_DIR.resolve())
                except Exception:
                    rel = Path(item.name)
                arcname = str(rel)
                if arcname in written:
                    continue
                zf.write(item, arcname=arcname)
                written.add(arcname)

    if not written:
        zip_path.unlink(missing_ok=True)
        raise HTTPException(status_code=404, detail="Se encontraron rutas, pero no contenían archivos descargables.")
    return zip_path


@app.get("/", response_class=HTMLResponse)
def index():
    index_path = TEMPLATES_DIR / "index.html"
    if not index_path.exists():
        return HTMLResponse("<h1>SATyS CRT</h1><p>No existe web/templates/index.html</p>", status_code=200)
    return HTMLResponse(index_path.read_text(encoding="utf-8"))


@app.get("/api/health")
def health():
    return {
        "ok": True,
        "project": PROJECT_NAME,
        "project_dir": str(PROJECT_DIR),
        "logs_dir": str(LOGS_DIR),
        "estado_json": str(ESTADO_JSON),
        "manual_allowed": os.getenv("SATYS_API_ALLOW_MANUAL", "0") == "1",
        "repair_allowed": os.getenv("SATYS_API_ALLOW_REPAIR", "0") == "1",
        "start_allowed": os.getenv("SATYS_API_ALLOW_START", "0") == "1",
        "timer_edit_allowed": os.getenv("SATYS_API_ALLOW_TIMER_EDIT", "0") == "1",
    }


@app.get("/api/config")
def config():
    return _get_config() | health()


@app.get("/api/estado")
def estado():
    if not ESTADO_JSON.exists():
        return {"running": False, "stage": "sin_estado", "mensaje": "Aún no hay estado_actual.json"}
    return _read_json(ESTADO_JSON)


@app.get("/api/resumen/ultimo")
def resumen_ultimo():
    return _read_json(RESUMEN_LATEST)


@app.get("/api/systemd")
def systemd_status():
    return _service_status()


@app.get("/api/archivos")
def archivos():
    return {
        "excel_control": _file_info(EXCEL_CONTROL),
        "excel_consolidado": _file_info(EXCEL_CONSOLIDADO),
        "output": _dir_info(OUTPUT_DIR),
        "descargas": _dir_info(DESCARGAS_DIR),
        "logs": _dir_info(LOGS_DIR),
        "registros_diarios": _dir_info(REGISTROS_DIR),
    }


@app.get("/api/historial")
def historial():
    daily: list[dict[str, Any]] = []
    for path in sorted(LOGS_DIR.glob("monitor_registros_*.json"), key=lambda p: p.stat().st_mtime, reverse=True):
        if path.name == "monitor_registros_ultimo.json":
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            data = {}
        daily.append({
            "tipo": "diaria",
            "archivo": path.name,
            "fecha": data.get("fecha_ejecucion") or datetime.fromtimestamp(path.stat().st_mtime).isoformat(timespec="seconds"),
            "ok": data.get("ok"),
            "mensaje": data.get("mensaje", ""),
            "total_satys": data.get("total_satys") or data.get("total_registros_satys") or data.get("registros_satys"),
            "total_nuevos": data.get("total_nuevos") or data.get("registros_nuevos"),
            "return_code_main": data.get("return_code_main"),
        })
    manual = _read_json_default(MANUAL_HISTORIAL_JSON, [])
    if not isinstance(manual, list):
        manual = []
    return {"daily": daily[:50], "manual": list(reversed(manual[-50:]))}


@app.get("/api/log/ultimo", response_class=PlainTextResponse)
def log_ultimo(tail: int = Query(default=300, ge=1, le=5000), tipo: str = Query(default="diario")):
    if tipo == "reparacion":
        log_path = _latest_repair_log_path()
    elif tipo == "manual":
        log_path = _latest_manual_log_path()
    else:
        log_path = _latest_daily_log_path()
    if not log_path:
        raise HTTPException(status_code=404, detail=f"No se encontró ningún log tipo {tipo}")
    return _tail_lines(log_path, tail=tail)


@app.get("/api/log/descargar")
def descargar_log(tipo: str = Query(default="diario")):
    if tipo == "reparacion":
        log_path = _latest_repair_log_path()
    elif tipo == "manual":
        log_path = _latest_manual_log_path()
    else:
        log_path = _latest_daily_log_path()
    if not log_path:
        raise HTTPException(status_code=404, detail="No se encontró ningún log para descargar")
    return FileResponse(path=str(log_path), filename=log_path.name, media_type="text/plain")


@app.get("/api/resumen/descargar")
def descargar_resumen():
    if not RESUMEN_LATEST.exists():
        raise HTTPException(status_code=404, detail="No existe monitor_registros_ultimo.json")
    return FileResponse(path=str(RESUMEN_LATEST), filename=RESUMEN_LATEST.name, media_type="application/json")


@app.get("/api/download/excel")
def download_excel():
    if not EXCEL_CONTROL.exists():
        raise HTTPException(status_code=404, detail="No existe TrámitesCRT.xlsx")
    return FileResponse(
        path=str(EXCEL_CONTROL),
        filename="TrámitesCRT.xlsx",
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )


@app.get("/api/download/consolidado")
def download_consolidado():
    if not EXCEL_CONSOLIDADO.exists():
        raise HTTPException(status_code=404, detail="No existe output/Folios_Datos_Completos.xlsx")
    return FileResponse(
        path=str(EXCEL_CONSOLIDADO),
        filename="Folios_Datos_Completos.xlsx",
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )


@app.get("/api/download/output")
def download_output_zip():
    zip_path = _zip_dir(OUTPUT_DIR, "satys_output")
    return FileResponse(path=str(zip_path), filename=zip_path.name, media_type="application/zip")


@app.get("/api/download/descargas")
def download_descargas_zip():
    zip_path = _zip_dir(DESCARGAS_DIR, "satys_descargas")
    return FileResponse(path=str(zip_path), filename=zip_path.name, media_type="application/zip")


@app.get("/api/registros/{registro}/buscar")
def buscar_registro(registro: str, tipo: str = Query(default="auto")):
    registro_norm = _normalizar_registro_api(registro)
    paths = _buscar_carpetas_registro(registro_norm, tipo=tipo)
    return {
        "ok": True,
        "registro": registro_norm,
        "tipo": tipo,
        "total": len(paths),
        "items": [_path_info_for_registro(p) for p in paths],
    }


@app.get("/api/registros/{registro}/download")
def descargar_registro(registro: str, tipo: str = Query(default="auto")):
    registro_norm = _normalizar_registro_api(registro)
    paths = _buscar_carpetas_registro(registro_norm, tipo=tipo)
    if not paths:
        raise HTTPException(status_code=404, detail=f"No se encontraron carpetas o metadata para {registro_norm}.")
    zip_path = _zip_paths(paths, f"satys_{registro_norm.replace('-', '_')}")
    return FileResponse(path=str(zip_path), filename=zip_path.name, media_type="application/zip")


@app.get("/api/manual/estado")
def manual_estado():
    return _refresh_manual_state()


@app.post("/api/manual/procesar")
async def manual_procesar(
    archivo: UploadFile = File(...),
    tipo_txt: str = Form("registros"),
    workers: int = Form(6),
    headless: bool = Form(True),
):
    if os.getenv("SATYS_API_ALLOW_MANUAL", "0") != "1":
        raise HTTPException(
            status_code=403,
            detail="Corrida manual deshabilitada. Define SATYS_API_ALLOW_MANUAL=1 en satys-api.service para permitirla.",
        )
    tipo_txt = (tipo_txt or "registros").strip().lower()
    if tipo_txt not in {"registros", "folios"}:
        raise HTTPException(status_code=400, detail="tipo_txt debe ser 'registros' o 'folios'.")
    workers = max(1, min(int(workers or 6), 16))

    estado_actual = _refresh_manual_state()
    if estado_actual.get("running"):
        raise HTTPException(status_code=409, detail="Ya hay una corrida manual en ejecución.")

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    run_id = f"manual_{ts}"
    run_dir = RUNS_DIR / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    input_path = run_dir / "entrada.txt"
    content = await archivo.read()
    if not content.strip():
        raise HTTPException(status_code=400, detail="El TXT está vacío.")
    input_path.write_bytes(content)

    log_path = LOGS_DIR / f"{run_id}.log"
    python_exe = os.getenv("SATYS_PYTHON", sys.executable)
    cmd = [python_exe, str(PROJECT_DIR / "main_procesar.py")]
    if tipo_txt == "registros":
        cmd += ["--archivo-registro", str(input_path)]
    else:
        cmd += ["--archivo-folios", str(input_path)]
    cmd += [
        "--workers", str(workers),
        "--timeout-registro", os.getenv("SATYS_TIMEOUT_REGISTRO", "900"),
        "--reintentos-registro", os.getenv("SATYS_REINTENTOS_REGISTRO", "2"),
        "--workers-reintento", os.getenv("SATYS_WORKERS_REINTENTO", "2"),
    ]
    if headless:
        cmd.append("--headless")

    env = os.environ.copy()
    env.setdefault("PYTHONUNBUFFERED", "1")
    env.setdefault("PYTHONIOENCODING", "utf-8")
    log_file = log_path.open("w", encoding="utf-8", buffering=1)
    log_file.write(f"[{_now_iso()}] Iniciando corrida manual {run_id}\n")
    log_file.write("Comando: " + " ".join(cmd) + "\n\n")

    try:
        proc = subprocess.Popen(
            cmd,
            cwd=str(PROJECT_DIR),
            env=env,
            stdout=log_file,
            stderr=subprocess.STDOUT,
            text=True,
        )
    except Exception as exc:
        try:
            log_file.close()
        except Exception:
            pass
        raise HTTPException(status_code=500, detail=f"No se pudo iniciar main_procesar.py: {exc}") from exc

    estado = {
        "running": True,
        "ok": None,
        "run_id": run_id,
        "pid": proc.pid,
        "tipo_txt": tipo_txt,
        "workers": workers,
        "headless": headless,
        "started_at": _now_iso(),
        "input_file": str(input_path),
        "log_path": str(log_path),
        "cmd": cmd,
        "mensaje": "Corrida manual iniciada.",
    }
    _write_json(MANUAL_ESTADO_JSON, estado)
    history_item = {
        "run_id": run_id,
        "tipo": "manual",
        "tipo_txt": tipo_txt,
        "workers": workers,
        "headless": headless,
        "started_at": estado["started_at"],
        "input_file": str(input_path),
        "log_path": str(log_path),
        "cmd": cmd,
    }
    t = threading.Thread(target=_wait_manual_process, args=(proc, MANUAL_ESTADO_JSON, history_item, log_file), daemon=True)
    t.start()
    return estado


@app.post("/api/registros/procesar")
async def registros_procesar(
    archivo: UploadFile = File(...),
    workers: int = Form(6),
    headless: bool = Form(True),
):
    """Atajo para procesar un TXT que contiene únicamente números de registro."""
    return await manual_procesar(archivo=archivo, tipo_txt="registros", workers=workers, headless=headless)


@app.get("/api/reparacion-id/estado")
def reparacion_id_estado():
    return _refresh_repair_state()


@app.post("/api/reparacion-id/iniciar")
def reparacion_id_iniciar(payload: dict[str, Any] = Body(default_factory=dict)):
    if os.getenv("SATYS_API_ALLOW_REPAIR", "0") != "1":
        raise HTTPException(
            status_code=403,
            detail="Reparación deshabilitada. Define SATYS_API_ALLOW_REPAIR=1 en satys-api.service.",
        )
    estado_actual = _refresh_repair_state()
    if estado_actual.get("running"):
        raise HTTPException(status_code=409, detail="Ya hay una reparación de id_solicitante en ejecución.")

    reiniciar = bool(payload.get("reiniciar_cola", False))
    actualizar_salidas = bool(payload.get("actualizar_salidas", True))
    redescargar_archivos = bool(payload.get("redescargar_archivos", False))
    reintentos_raw = payload.get("reintentos", 2)
    reintentos = max(0, min(int(2 if reintentos_raw is None else reintentos_raw), 10))
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_path = LOGS_DIR / f"reparacion_id_{ts}.log"
    python_exe = os.getenv("SATYS_PYTHON", sys.executable)
    cmd = [python_exe, str(PROJECT_DIR / "reparar_id_solicitante.py"), "--reintentos", str(reintentos), "--headless"]
    if reiniciar:
        cmd.append("--reiniciar-cola")
    if not actualizar_salidas:
        cmd.append("--no-actualizar-salidas")
    if redescargar_archivos:
        cmd.append("--redescargar-archivos")

    env = os.environ.copy()
    env.setdefault("PYTHONUNBUFFERED", "1")
    env.setdefault("PYTHONIOENCODING", "utf-8")
    log_file = log_path.open("w", encoding="utf-8", buffering=1)
    log_file.write(f"[{_now_iso()}] Iniciando reparación de id_solicitante\n")
    log_file.write("Comando: " + " ".join(cmd) + "\n\n")
    try:
        proc = subprocess.Popen(cmd, cwd=str(PROJECT_DIR), env=env, stdout=log_file, stderr=subprocess.STDOUT, text=True, start_new_session=True)
    except Exception as exc:
        log_file.close()
        raise HTTPException(status_code=500, detail=f"No se pudo iniciar el reparador: {exc}") from exc

    estado = dict(estado_actual)
    estado.update({
        "running": True, "ok": None, "status": "starting", "pid": proc.pid,
        "started_at": _now_iso(), "finished_at": None, "log_path": str(log_path),
        "reiniciar_cola": reiniciar, "actualizar_salidas": actualizar_salidas,
        "redescargar_archivos": redescargar_archivos,
        "reintentos": reintentos, "cmd": cmd,
        "mensaje": "Reparación iniciada; el checkpoint se actualizará después de cada Registro.",
    })
    _write_json(REPAIR_ESTADO_JSON, estado)
    threading.Thread(target=_wait_repair_process, args=(proc, log_file), daemon=True).start()
    return estado


@app.post("/api/reparacion-id/detener")
def reparacion_id_detener():
    estado = _refresh_repair_state()
    pid = int(estado.get("pid") or 0)
    if not estado.get("running") or pid <= 0:
        return estado | {"mensaje": "No hay una reparación activa."}
    try:
        os.killpg(pid, signal.SIGTERM)
    except ProcessLookupError:
        pass
    except Exception:
        try:
            os.kill(pid, signal.SIGTERM)
        except Exception as exc:
            raise HTTPException(status_code=500, detail=f"No se pudo detener el proceso {pid}: {exc}") from exc
    estado.update({"status": "stopping", "mensaje": "Detención solicitada. El checkpoint permitirá reanudar desde el último Registro."})
    _write_json(REPAIR_ESTADO_JSON, estado)
    return estado


@app.post("/api/timer/hora")
def timer_hora(payload: dict[str, Any] = Body(...)):
    hora = str(payload.get("hora", "")).strip()
    if not re.fullmatch(r"([01]\d|2[0-3]):[0-5]\d", hora):
        raise HTTPException(status_code=400, detail="Hora inválida. Usa formato HH:MM en 24 horas.")
    cfg = _get_config()
    cfg["timer_hora"] = hora
    cfg["updated_at"] = _now_iso()
    _set_config(cfg)
    install = _install_timer_if_allowed(hora)
    return {"ok": True, "hora": hora, "install": install, "systemd": _service_status()}


@app.post("/api/proceso/iniciar")
def iniciar_proceso_manual_diario():
    if os.getenv("SATYS_API_ALLOW_START", "0") != "1":
        raise HTTPException(
            status_code=403,
            detail="Ejecución diaria manual deshabilitada. Define SATYS_API_ALLOW_START=1 si quieres permitirla.",
        )
    try:
        r = subprocess.run(["systemctl", "start", "--no-block", SERVICE_NAME], capture_output=True, text=True, timeout=20)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    if r.returncode != 0:
        raise HTTPException(status_code=500, detail=(r.stderr or r.stdout or "systemctl falló"))
    time.sleep(1)
    return {"ok": True, "service": SERVICE_NAME, "estado": _service_status()}


async def _stream_log(kind: str):
    current_path: Path | None = None
    pos = 0
    sent_initial = False

    while True:
        if kind == "reparacion":
            log_path = _latest_repair_log_path()
        elif kind == "manual":
            log_path = _latest_manual_log_path()
        else:
            log_path = _latest_daily_log_path()
        if not log_path:
            yield "event: status\ndata: Aún no hay log disponible\n\n"
            await asyncio.sleep(3)
            continue

        if current_path != log_path:
            current_path = log_path
            sent_initial = False
            pos = 0
            yield f"event: source\ndata: {current_path.name}\n\n"

        try:
            if not sent_initial:
                text = _tail_lines(current_path, tail=200)
                if text:
                    for line in text.splitlines():
                        yield f"data: {line}\n\n"
                pos = current_path.stat().st_size
                sent_initial = True
            else:
                size = current_path.stat().st_size
                if size < pos:
                    pos = 0
                if size > pos:
                    with current_path.open("r", encoding="utf-8", errors="replace") as f:
                        f.seek(pos)
                        chunk = f.read()
                        pos = f.tell()
                    for line in chunk.splitlines():
                        yield f"data: {line}\n\n"
        except Exception as exc:
            msg = str(exc).replace("\n", " ")
            yield f"event: error\ndata: {msg}\n\n"

        yield ": keep-alive\n\n"
        await asyncio.sleep(2)


@app.get("/api/log/stream")
async def log_stream(tipo: str = Query(default="diario")):
    kind = tipo if tipo in {"manual", "reparacion"} else "diario"
    return StreamingResponse(_stream_log(kind), media_type="text/event-stream")
