#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
estado_ejecucion.py
===================
Estado vivo del backend SATyS.

Este módulo escribe un JSON pequeño y atómico que puede leer después un
frontend FastAPI, Cockpit, cron de salud o cualquier script de monitoreo.
No depende de Flet ni de frameworks web.
"""

from __future__ import annotations

import json
import os
import socket
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any


def ahora_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


class EstadoEjecucion:
    """Escritor de estado JSON con escritura atómica."""

    def __init__(self, path: str | Path, proceso: str = "satys") -> None:
        self.path = Path(path)
        self.proceso = proceso
        self.started_at = ahora_iso()
        self.pid = os.getpid()
        self.hostname = socket.gethostname()
        self._data: dict[str, Any] = {
            "running": False,
            "proceso": proceso,
            "pid": self.pid,
            "hostname": self.hostname,
            "started_at": self.started_at,
            "updated_at": self.started_at,
            "stage": "inicializando",
        }

    def actualizar(self, **kwargs: Any) -> None:
        self._data.update(kwargs)
        self._data["running"] = kwargs.get("running", self._data.get("running", True))
        self._data["updated_at"] = ahora_iso()
        self._write_atomic(self._data)

    def finalizar(self, ok: bool, mensaje: str = "", **kwargs: Any) -> None:
        self._data.update(kwargs)
        self._data.update({
            "running": False,
            "ok": bool(ok),
            "mensaje": mensaje,
            "finished_at": ahora_iso(),
            "updated_at": ahora_iso(),
            "stage": "finalizado" if ok else "error",
        })
        self._write_atomic(self._data)

    def _write_atomic(self, data: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_name = tempfile.mkstemp(
            prefix=f".{self.path.name}.", suffix=".tmp", dir=str(self.path.parent)
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2, default=str)
                f.write("\n")
            os.replace(tmp_name, self.path)
        finally:
            try:
                if os.path.exists(tmp_name):
                    os.unlink(tmp_name)
            except Exception:
                pass


def leer_estado(path: str | Path) -> dict[str, Any] | None:
    try:
        p = Path(path)
        if not p.exists():
            return None
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None
