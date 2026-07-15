#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""
=============================================================
  proceso_lock.py — Bloqueo (mutex) compartido entre equipos
=============================================================
PROBLEMA QUE RESUELVE:
  Varias equipos del equipo comparten la misma carpeta de red
  (por ejemplo /data/satys/Automatizacion-SATyS/.lock) y podrían intentar correr el
  pipeline (main_procesar.py, automatizar_registros_diario.py o
  la UI) al mismo tiempo. Eso puede provocar:
    - Dos sesiones abiertas a la vez en el portal SATyS.
    - Colisiones al escribir TrámitesCRT.xlsx.
    - Archivos de /output y /descargas mezclados o pisados a medias.

CÓMO FUNCIONA:
  Antes de arrancar, cada equipo intenta crear un archivo de
  bloqueo ("satys_proceso.lock") dentro de una carpeta COMPARTIDA
  en red. Si el archivo ya existe y su "latido" (heartbeat) es
  reciente, se asume que otra equipo está trabajando y se cancela
  la ejecución con un mensaje claro (quién, en qué equipo, desde
  cuándo). Si el archivo no existe, o su latido es muy viejo (el
  proceso murió sin limpiar, ej. apagón o cierre forzado), se toma
  el bloqueo con normalidad.

  Mientras el proceso corre, un hilo en segundo plano actualiza el
  latido cada pocos segundos. Al terminar (bien o con error) se
  borra el archivo de bloqueo automáticamente (vía atexit).

CONFIGURAR LA CARPETA COMPARTIDA (se usa la primera que aplique):
  1. Variable de entorno SATYS_LOCK_DIR, por ejemplo:
       export SATYS_LOCK_DIR=/data/satys/Automatizacion-SATyS/.lock
  2. Editar "ruta_carpeta_compartida.txt" (junto a este script) y
     escribir ahí la ruta de red, en una sola línea sin "#".
  3. Si no se configura nada, el bloqueo se guarda LOCALMENTE
     (carpeta ".lock_local" dentro del proyecto) y SOLO protege
     esta equipo — no protege contra las otras 3.

USO TÍPICO:
    from proceso_lock import ProcesoLock, LockOcupadoError

    lock = ProcesoLock(proceso="main_procesar.py")
    try:
        lock.adquirir()
    except LockOcupadoError as ex:
        print(f"No se puede iniciar: {ex}")
        sys.exit(1)
    # El bloqueo se libera solo al terminar el programa (atexit).

REUTILIZACIÓN ENTRE PROCESOS DE LA MISMA CADENA:
  Si automatizar_registros_diario.py ya tomó el bloqueo y luego
  lanza main_procesar.py como subproceso, este último HEREDA el
  bloqueo (vía variable de entorno) en lugar de pelear por uno
  nuevo o bloquearse a sí mismo.

LÍMITES CONOCIDOS (honestidad técnica):
  Este mecanismo usa un archivo en una carpeta de red compartida
  por Windows (SMB), no una base de datos. No es un lock atómico
  perfecto a nivel de sistema operativo, pero para un equipo de
  4 personas que arrancan el proceso manualmente o una vez al día
  por tarea programada, el riesgo de colisión exacta (mismo
  instante) es prácticamente nulo. No usar esto para escenarios de
  alta concurrencia.
=============================================================
"""

from __future__ import annotations

import atexit
import getpass
import json
import os
import socket
import threading
import time
import uuid
from datetime import datetime
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parent
LOCK_FILENAME = "satys_proceso.lock"
RUTA_CONFIG_FILE = PROJECT_DIR / "ruta_carpeta_compartida.txt"
LOCK_DIR_LOCAL_DEFAULT = PROJECT_DIR / ".lock_local"

# Variables de entorno usadas para "heredar" el bloqueo entre un
# proceso padre (ej. el monitor diario) y los subprocesos que lanza
# (ej. main_procesar.py), para que no se bloqueen entre sí.
TOKEN_ENV_VAR = "SATYS_LOCK_TOKEN"
INFO_ENV_VAR = "SATYS_LOCK_INFO"

HEARTBEAT_INTERVALO_SEG = 20
LOCK_STALE_SEG_DEFAULT = 3 * 60 * 60  # 3 horas sin latido => se considera abandonado


class LockOcupadoError(Exception):
    """Se lanza cuando otra equipo ya tiene el bloqueo activo."""


def _resolver_carpeta_lock() -> Path:
    """Decide dónde vive el archivo de bloqueo, en orden de prioridad."""
    env_dir = os.environ.get("SATYS_LOCK_DIR", "").strip()
    if env_dir:
        return Path(env_dir)

    if RUTA_CONFIG_FILE.exists():
        try:
            texto = RUTA_CONFIG_FILE.read_text(encoding="utf-8", errors="replace")
        except Exception:
            texto = ""
        for linea in texto.splitlines():
            linea = linea.strip()
            if not linea or linea.startswith("#"):
                continue
            return Path(linea)

    return LOCK_DIR_LOCAL_DEFAULT


def _ahora_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _segundos_desde(iso_texto: str) -> float | None:
    if not iso_texto:
        return None
    try:
        entonces = datetime.fromisoformat(iso_texto)
        return (datetime.now() - entonces).total_seconds()
    except Exception:
        return None


class ProcesoLock:
    """
    Mutex de "un solo proceso SATyS a la vez", compartido entre
    equipos vía un archivo en una carpeta de red.

    Uso:
        lock = ProcesoLock(proceso="main_procesar.py")
        lock.adquirir()   # lanza LockOcupadoError si está ocupado
        ...
        lock.liberar()    # opcional; también se libera solo al salir
    """

    def __init__(self, proceso: str, lock_dir: Path | str | None = None,
                 stale_seg: int = LOCK_STALE_SEG_DEFAULT):
        self.proceso = proceso
        self.lock_dir = Path(lock_dir) if lock_dir else _resolver_carpeta_lock()
        self.lock_path = self.lock_dir / LOCK_FILENAME
        self.stale_seg = stale_seg
        self.token: str | None = None
        self.heredado = False
        self._heartbeat_thread: threading.Thread | None = None
        self._detener_heartbeat = threading.Event()
        self._liberado = False

    # ── Lectura de estado (no adquiere nada; segura de llamar siempre) ──
    def leer_estado(self) -> dict | None:
        """Devuelve el contenido del lock actual (si existe), o None."""
        try:
            if not self.lock_path.exists():
                return None
            data = json.loads(self.lock_path.read_text(encoding="utf-8"))
        except Exception:
            return None
        edad = _segundos_desde(data.get("ultimo_latido", ""))
        data["_edad_latido_seg"] = edad
        data["_vigente"] = (edad is not None and edad < self.stale_seg)
        return data

    def _mensaje_ocupado(self, info: dict) -> str:
        edad_seg = info.get("_edad_latido_seg") or 0
        edad_min = int(edad_seg / 60)
        return (
            f"Ya hay un proceso SATyS corriendo en la equipo de "
            f"{info.get('usuario', '?')} ({info.get('equipo', '?')}), "
            f"iniciado el {info.get('inicio', '?')} "
            f"(último latido hace {edad_min} min). "
            f"Proceso: {info.get('proceso', '?')}. "
            f"Espera a que termine antes de iniciar otro."
        )

    # ── Adquisición ──────────────────────────────────────────────────
    def adquirir(self) -> None:
        """
        Intenta tomar el bloqueo. Lanza LockOcupadoError si otra equipo
        (o este mismo equipo, en otra ejecución) ya lo tiene activo.

        Si un proceso ANCESTRO ya tomó el bloqueo (por ejemplo, el
        monitor diario que luego lanza main_procesar.py), este proceso
        simplemente lo hereda sin pelear por uno nuevo.
        """
        heredado_token = os.environ.get(TOKEN_ENV_VAR, "").strip()
        if heredado_token:
            self.heredado = True
            self.token = heredado_token
            return

        self.lock_dir.mkdir(parents=True, exist_ok=True)

        ultimo_info_conocido = None
        for intento in range(3):
            info_existente = self.leer_estado()
            ultimo_info_conocido = info_existente

            if info_existente is not None and info_existente.get("_vigente"):
                raise LockOcupadoError(self._mensaje_ocupado(info_existente))

            if info_existente is not None and not info_existente.get("_vigente"):
                # Bloqueo abandonado (el proceso murió sin limpiar) -> se libera.
                try:
                    self.lock_path.unlink(missing_ok=True)
                except Exception:
                    pass

            self.token = str(uuid.uuid4())
            payload = {
                "token": self.token,
                "proceso": self.proceso,
                "usuario": getpass.getuser(),
                "equipo": socket.gethostname(),
                "pid": os.getpid(),
                "inicio": _ahora_iso(),
                "ultimo_latido": _ahora_iso(),
            }
            try:
                # Creación EXCLUSIVA: falla si otra equipo lo creó justo ahora
                # (es la parte más "atómica" de este mecanismo).
                fd = os.open(str(self.lock_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                with os.fdopen(fd, "w", encoding="utf-8") as f:
                    json.dump(payload, f, ensure_ascii=False, indent=2)
                break  # ¡Bloqueo tomado!
            except FileExistsError:
                time.sleep(0.3 + intento * 0.4)  # otra equipo nos ganó justo ahora
                continue
        else:
            info_final = self.leer_estado() or ultimo_info_conocido
            if info_final:
                raise LockOcupadoError(self._mensaje_ocupado(info_final))
            raise LockOcupadoError(
                "No se pudo tomar el bloqueo compartido tras varios intentos "
                "(¿la carpeta de red no está disponible?)."
            )

        os.environ[TOKEN_ENV_VAR] = self.token
        os.environ[INFO_ENV_VAR] = f"{payload['usuario']}@{payload['equipo']}"
        self._iniciar_heartbeat()
        atexit.register(self.liberar)

    # ── Latido periódico (mientras el proceso sigue vivo) ───────────
    def _iniciar_heartbeat(self) -> None:
        def loop():
            while not self._detener_heartbeat.wait(HEARTBEAT_INTERVALO_SEG):
                self._actualizar_latido()

        self._heartbeat_thread = threading.Thread(target=loop, daemon=True)
        self._heartbeat_thread.start()

    def _actualizar_latido(self) -> None:
        try:
            data = json.loads(self.lock_path.read_text(encoding="utf-8"))
            if data.get("token") != self.token:
                return  # ya no es nuestro lock (no debería ocurrir)
            data["ultimo_latido"] = _ahora_iso()
            self.lock_path.write_text(
                json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
            )
        except Exception:
            pass  # si la red falla un momento, se reintenta en el próximo latido

    # ── Liberación ────────────────────────────────────────────────────
    def liberar(self) -> None:
        """Libera el bloqueo. Se registra automáticamente con atexit,
        pero también se puede llamar explícitamente al terminar."""
        if self._liberado or self.heredado:
            return
        self._liberado = True
        self._detener_heartbeat.set()
        try:
            data = json.loads(self.lock_path.read_text(encoding="utf-8"))
            if data.get("token") == self.token:
                self.lock_path.unlink(missing_ok=True)
        except Exception:
            pass
        os.environ.pop(TOKEN_ENV_VAR, None)
        os.environ.pop(INFO_ENV_VAR, None)


# ── Funciones sueltas, pensadas para la UI (consultar sin adquirir) ──

def consultar_estado_lock() -> dict | None:
    """Para que la UI muestre si hay un proceso corriendo, sin tomar el bloqueo."""
    return ProcesoLock(proceso="consulta").leer_estado()


def forzar_liberar_lock() -> bool:
    """Borra el archivo de bloqueo manualmente (botón de emergencia en la UI,
    para usarse solo si un proceso quedó "atorado" tras un cierre anormal)."""
    lock_path = _resolver_carpeta_lock() / LOCK_FILENAME
    try:
        lock_path.unlink(missing_ok=True)
        return True
    except Exception:
        return False


def ruta_carpeta_lock_actual() -> Path:
    """Ruta efectiva donde se guarda hoy el archivo de bloqueo (para mostrar en UI)."""
    return _resolver_carpeta_lock()
