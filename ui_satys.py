#!/usr/bin/env python3
r"""
=============================================================
  UI — SATyS CRT / PROPUESTA 2
=============================================================
Interfaz web local para ejecutar el flujo principal:

  Entrada:
    - Un archivo TXT con registros CRT o folios SATyS.

  Salidas esperadas:
    - TrámitesCRT.xlsx
    - output/Folios_Datos_Completos.xlsx
    - Carpetas organizadas dentro de output/

  Además muestra Resultados debajo del Log del proceso y conserva el acceso lateral Resumen.

Nota importante:
  Esta UI está pensada para ejecutarse en navegador web local con Flet.
  Cuando abre Explorador de Windows o el diálogo Examinar, usa una rutina
  de foco con Win32 para que la ventana se abra delante del navegador.

Uso:
  .\python-3.11.9-embed-amd64\python.exe ui_satys_propuesta_2.py
=============================================================
"""

from __future__ import annotations

import csv
import io
import json
import os
import queue
import shlex
import subprocess
import sys
import threading
import time
from datetime import datetime
from pathlib import Path

import flet as ft

# ════════════════════════════════════════════════════════
#  PALETA VISUAL CRT / SATyS
# ════════════════════════════════════════════════════════

PAGE_BG            = "#F1F3F5"
SIDEBAR_BG         = "#D9E7E7"
SIDEBAR_SEL_BG     = "#C3D6D7"
CARD_BG            = "#FFFFFF"
SOFT_CARD_BG       = "#F8FAFA"
TABLE_HEADER_BG    = "#F4F5F6"
BORDER_COLOR       = "#E3E7E9"
DIVIDER_COLOR      = "#ECEEEF"

TEXT_DARK          = "#222426"
TEXT_GRAY          = "#5C6366"
TEXT_MUTED         = "#8A9295"

TEAL_PRIMARY       = "#156E78"
TEAL_DARK          = "#0F5860"
TEAL_PROGRESS      = "#0A8C95"
GREEN_OK           = "#4A914A"
GREEN_DARK         = "#3D7A3D"
ORANGE_WARN        = "#C87D20"
RED_ERR            = "#B23B3B"
BLUE_INFO          = "#2563A8"

LOG_BG             = "#16202A"
LOG_TEXT           = "#D4E0EC"
LOG_SUCCESS        = "#5DBF6E"
LOG_ERROR          = "#E06060"
LOG_WARNING        = "#E0B860"
LOG_INFO           = "#60A8D4"
LOG_MUTED          = "#5A7080"

# ════════════════════════════════════════════════════════
#  CONFIGURACIÓN
# ════════════════════════════════════════════════════════

DEFAULT_SCRIPT       = "main_procesar.py"
DEFAULT_PYTHON       = r"python-3.11.9-embed-amd64\python.exe"
DEFAULT_WORKERS      = 6
DEFAULT_HEADLESS     = True
CONFIG_FILE          = Path("config_satys_ui.json")
DESCARGAS_DIR        = Path("descargas")
OUTPUT_DIR           = Path("output")
EXCEL_CONTROL        = Path("TrámitesCRT.xlsx")
EXCEL_CONSOLIDADO    = OUTPUT_DIR / "Folios_Datos_Completos.xlsx"
PROCESAMIENTO_LOG    = DESCARGAS_DIR / "procesamiento_log.json"
RESUMEN_GLOBAL_LOG   = DESCARGAS_DIR / "resumen_global.json"
DESCARGA_LOG          = DESCARGAS_DIR / "descarga_log.json"
CREDENCIALES_FILE    = Path(os.getenv("SATYS_CREDENTIALS_FILE", str(Path.home() / ".satys" / "credenciales.txt")))

NAV_ITEMS = [
    ("Procesar",      ft.Icons.PLAY_CIRCLE_OUTLINE, ft.Icons.PLAY_CIRCLE),
    ("Resumen",       ft.Icons.SUMMARIZE_OUTLINED,    ft.Icons.SUMMARIZE_OUTLINED),
    ("Salidas",       ft.Icons.FOLDER_COPY_OUTLINED, ft.Icons.FOLDER_COPY),
    ("Historial",     ft.Icons.HISTORY,              ft.Icons.HISTORY),
    ("Configuración", ft.Icons.SETTINGS_OUTLINED,    ft.Icons.SETTINGS),
]

# ════════════════════════════════════════════════════════
#  HELPERS GENERALES
# ════════════════════════════════════════════════════════


def _ts() -> str:
    return datetime.now().strftime("%H:%M:%S")


def _dt() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _load_config() -> dict:
    if CONFIG_FILE.exists():
        try:
            return json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}


def _save_config(data: dict) -> None:
    try:
        CONFIG_FILE.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    except Exception:
        pass


def _python_exe_default() -> str:
    portable = Path(DEFAULT_PYTHON)
    if portable.exists():
        return str(portable)
    return sys.executable


def _line_count_txt(path: str) -> int:
    p = Path(path)
    if not p.exists() or not p.is_file():
        return 0
    total = 0
    try:
        with open(p, "r", encoding="utf-8", errors="ignore") as f:
            for line in f:
                if line.strip():
                    total += 1
    except Exception:
        return 0
    return total


def _read_satys_credentials() -> tuple[str, str, str]:
    """Lee credenciales desde ~/.satys/credenciales.txt o variables de entorno."""
    usuario = ""
    password = ""
    source = f"Archivo esperado: {CREDENCIALES_FILE}"

    try:
        if CREDENCIALES_FILE.exists():
            with CREDENCIALES_FILE.open("r", encoding="utf-8") as f:
                usuario = f.readline().strip()
                password = f.readline().strip()
            source = f"Credenciales leídas automáticamente desde: {CREDENCIALES_FILE}"
    except Exception as ex:
        source = f"No pude leer {CREDENCIALES_FILE}: {ex}"

    if not usuario:
        usuario = os.getenv("SATYS_USER", "").strip()
    if not password:
        password = os.getenv("SATYS_PASS", "").strip()
    if (usuario or password) and not CREDENCIALES_FILE.exists():
        source = "Credenciales tomadas de variables de entorno SATYS_USER/SATYS_PASS."

    return usuario, password, source


def _write_satys_credentials(usuario: str, password: str) -> tuple[bool, str]:
    """Guarda credenciales con el formato de 2 líneas de la plantilla."""
    try:
        CREDENCIALES_FILE.parent.mkdir(parents=True, exist_ok=True)
        CREDENCIALES_FILE.write_text(
            f"{usuario.strip()}\n{password.strip()}\n",
            encoding="utf-8",
        )
        return True, f"Credenciales guardadas en: {CREDENCIALES_FILE}"
    except Exception as ex:
        return False, f"No pude guardar credenciales en {CREDENCIALES_FILE}: {ex}"


def _command_to_string(args: list[str]) -> str:
    if os.name == "nt":
        return subprocess.list2cmdline(args)
    return shlex.join(args)


def _color_for_line(line: str) -> str:
    l = line.lower()
    if any(t in l for t in ["✅", "éxito", "exitoso", "ok", "completad", "guardad", "listo", "copiad"]):
        return LOG_SUCCESS
    if any(t in l for t in ["❌", "error", "crítico", "critico", "falló", "fallo", "fail", "critical"]):
        return LOG_ERROR
    if any(t in l for t in ["⚠", "warn", "advertencia", "revisión", "revision", "empate", "baja"]):
        return LOG_WARNING
    if any(t in l for t in ["📥", "📄", "📊", "📁", "🔍", "🆔", "🌐", "💾", "📅", "➕", "📋", "🗂"]):
        return LOG_INFO
    if any(t in l for t in ["───", "═══", "parte", "folio", "registro", "procesando", "iniciando"]):
        return "#A0BBCC"
    return LOG_TEXT


# ════════════════════════════════════════════════════════
#  WINDOWS: ABRIR DIÁLOGOS Y EXPLORADOR EN PRIMER PLANO
# ════════════════════════════════════════════════════════


def _permitir_ventana_al_frente() -> None:
    """Best effort: desbloquea el foco de Windows antes de abrir Explorer/dialogs."""
    if os.name != "nt":
        return
    try:
        import ctypes

        user32 = ctypes.windll.user32
        # Truco clásico: presionar y soltar ALT para liberar ForegroundLockTimeout.
        user32.keybd_event(0x12, 0, 0, 0)  # VK_MENU / ALT down
        user32.keybd_event(0x12, 0, 2, 0)  # ALT up
        user32.AllowSetForegroundWindow(-1)
        time.sleep(0.05)
    except Exception:
        pass


def _abrir_ruta_al_frente(ruta: str | Path, seleccionar_archivo: bool = False) -> None:
    """Abre carpeta/archivo en Explorer intentando que quede delante del navegador."""
    p = Path(ruta)
    try:
        if p.suffix and not p.exists():
            p.parent.mkdir(parents=True, exist_ok=True)
        elif not p.suffix:
            p.mkdir(parents=True, exist_ok=True)
    except Exception:
        pass

    _permitir_ventana_al_frente()

    if os.name == "nt":
        try:
            path_str = str(p.resolve())
            if seleccionar_archivo or p.is_file():
                subprocess.Popen(["explorer", "/select,", path_str])
            else:
                subprocess.Popen(["explorer", "/n,", path_str])
            _permitir_ventana_al_frente()
            return
        except Exception:
            pass

    try:
        if os.name == "nt":
            os.startfile(str(p.resolve()))  # type: ignore[attr-defined]
        elif sys.platform == "darwin":
            subprocess.Popen(["open", str(p.resolve())])
        else:
            subprocess.Popen(["xdg-open", str(p.resolve())])
    except Exception:
        pass


def _abrir_dialogo_archivo_al_frente(
    titulo: str,
    filtro: str,
    initial_dir: str | None = None,
) -> str:
    """
    Diálogo nativo de selección de archivo.
    En Windows usa Win32 para que el diálogo salga por encima del navegador local.
    """
    _permitir_ventana_al_frente()

    if os.name != "nt":
        return ""

    try:
        import ctypes
        from ctypes import wintypes

        GetOpenFileNameW = ctypes.windll.comdlg32.GetOpenFileNameW

        class OPENFILENAMEW(ctypes.Structure):
            _fields_ = [
                ("lStructSize", wintypes.DWORD),
                ("hwndOwner", wintypes.HWND),
                ("hInstance", wintypes.HINSTANCE),
                ("lpstrFilter", wintypes.LPCWSTR),
                ("lpstrCustomFilter", wintypes.LPWSTR),
                ("nMaxCustFilter", wintypes.DWORD),
                ("nFilterIndex", wintypes.DWORD),
                ("lpstrFile", wintypes.LPWSTR),
                ("nMaxFile", wintypes.DWORD),
                ("lpstrFileTitle", wintypes.LPWSTR),
                ("nMaxFileTitle", wintypes.DWORD),
                ("lpstrInitialDir", wintypes.LPCWSTR),
                ("lpstrTitle", wintypes.LPCWSTR),
                ("Flags", wintypes.DWORD),
                ("nFileOffset", wintypes.WORD),
                ("nFileExtension", wintypes.WORD),
                ("lpstrDefExt", wintypes.LPCWSTR),
                ("lCustData", wintypes.LPARAM),
                ("lpfnHook", ctypes.c_void_p),
                ("lpTemplateName", wintypes.LPCWSTR),
                ("pvReserved", ctypes.c_void_p),
                ("dwReserved", wintypes.DWORD),
                ("FlagsEx", wintypes.DWORD),
            ]

        file_buffer = ctypes.create_unicode_buffer(32768)
        filter_nulls = filtro.replace("|", "\0") + "\0\0"

        ofn = OPENFILENAMEW()
        ofn.lStructSize = ctypes.sizeof(OPENFILENAMEW)
        ofn.hwndOwner = ctypes.windll.user32.GetForegroundWindow()
        ofn.lpstrFilter = filter_nulls
        ofn.lpstrFile = ctypes.cast(file_buffer, wintypes.LPWSTR)
        ofn.nMaxFile = len(file_buffer)
        ofn.lpstrInitialDir = initial_dir or str(Path.cwd())
        ofn.lpstrTitle = titulo
        # OFN_EXPLORER | OFN_PATHMUSTEXIST | OFN_FILEMUSTEXIST | OFN_NOCHANGEDIR
        ofn.Flags = 0x00080000 | 0x00000800 | 0x00001000 | 0x00000008

        ok = GetOpenFileNameW(ctypes.byref(ofn))
        _permitir_ventana_al_frente()
        if ok:
            return file_buffer.value
    except Exception:
        pass

    return ""


# ════════════════════════════════════════════════════════
#  APLICACIÓN PRINCIPAL
# ════════════════════════════════════════════════════════


class SATySApp:
    def _safe_bind(self, control, event_name: str, handler):
        """Asigna eventos fuera del constructor para compatibilidad con Flet viejo."""
        try:
            setattr(control, event_name, handler)
        except Exception:
            pass

    def __init__(self, page: ft.Page):
        self.page = page
        self.active_nav = "Procesar"
        self._sidebar_visible = True
        self._running = False
        self._process: subprocess.Popen | None = None
        self._log_queue: queue.Queue = queue.Queue()
        self._log_lines: list[str] = []
        self._items_detectados = 0
        self._historial: list[dict] = []
        self._last_summary_refresh_ts = 0.0
        self._summary_refresh_interval = 1.0
        self._run_started_at: float | None = None
        self._run_input_kind = ""
        self._run_input_items: list[str] = []
        self._summary_refresh_thread_active = False

        # Controles persistentes para no reconstruir la pantalla Procesar durante
        # la actualización automática de Resultados. Esto evita que el scroll
        # regrese arriba mientras el usuario está revisando el log/resultados.
        self._procesar_screen: ft.Control | None = None
        self._procesar_summary_slot = ft.Container()

        cfg = _load_config()
        self._cfg = cfg

        default_txt = cfg.get("txt_path", "")
        if not default_txt:
            if Path("registros.txt").exists():
                default_txt = "registros.txt"
            elif Path("folios.txt").exists():
                default_txt = "folios.txt"

        # Entrada principal
        self.dd_tipo_txt = ft.Dropdown(
            label="El TXT contiene",
            value=cfg.get("input_kind", "registros"),
            options=[
                ft.dropdown.Option("registros", "Números de registro CRT"),
                ft.dropdown.Option("folios", "Folios SATyS"),
            ],
            width=260,
            height=48,
            text_size=13,
            border_color=BORDER_COLOR,
            focused_border_color=TEAL_PRIMARY,
            border_radius=ft.BorderRadius.all(10),
            content_padding=ft.Padding.symmetric(vertical=4, horizontal=10),
        )
        self.txt_archivo = ft.TextField(
            value=default_txt,
            hint_text="Pega la ruta del archivo .txt o usa Examinar",
            text_size=13,
            height=48,
            expand=True,
            border_color=BORDER_COLOR,
            focused_border_color=TEAL_PRIMARY,
            border_radius=ft.BorderRadius.all(10),
            content_padding=ft.Padding.symmetric(vertical=8, horizontal=12),
        )

        cred_usuario, cred_password, cred_source = _read_satys_credentials()
        self.txt_satys_user = ft.TextField(
            label="Usuario SATyS",
            value=cred_usuario,
            hint_text="correo institucional",
            text_size=13,
            height=48,
            expand=True,
            border_color=BORDER_COLOR,
            focused_border_color=TEAL_PRIMARY,
            border_radius=ft.BorderRadius.all(10),
            content_padding=ft.Padding.symmetric(vertical=6, horizontal=12),
        )
        self.txt_satys_pass = ft.TextField(
            label="Contraseña SATyS",
            value=cred_password,
            hint_text="contraseña",
            password=True,
            can_reveal_password=True,
            text_size=13,
            height=48,
            expand=True,
            border_color=BORDER_COLOR,
            focused_border_color=TEAL_PRIMARY,
            border_radius=ft.BorderRadius.all(10),
            content_padding=ft.Padding.symmetric(vertical=6, horizontal=12),
        )
        self.cred_status = ft.Text(cred_source, size=11, color=TEXT_MUTED)

        self.txt_workers = ft.TextField(
            value=str(cfg.get("workers", DEFAULT_WORKERS)),
            label="Ventanas",
            width=110,
            height=48,
            text_size=13,
            border_color=BORDER_COLOR,
            focused_border_color=TEAL_PRIMARY,
            border_radius=ft.BorderRadius.all(10),
            content_padding=ft.Padding.symmetric(vertical=4, horizontal=10),
        )
        self.sw_headless = ft.Switch(
            label="Modo rápido sin navegador visible",
            value=cfg.get("headless", DEFAULT_HEADLESS),
            active_color=TEAL_PRIMARY,
        )

        # Configuración avanzada
        self.txt_python = ft.TextField(
            label="Python portable",
            value=cfg.get("python_path", _python_exe_default()),
            text_size=13,
            height=44,
            expand=True,
            border_color=BORDER_COLOR,
            focused_border_color=TEAL_PRIMARY,
            border_radius=ft.BorderRadius.all(8),
            content_padding=ft.Padding.symmetric(vertical=6, horizontal=10),
        )
        self.txt_script = ft.TextField(
            label="Script principal",
            value=cfg.get("script_path", DEFAULT_SCRIPT),
            text_size=13,
            height=44,
            expand=True,
            border_color=BORDER_COLOR,
            focused_border_color=TEAL_PRIMARY,
            border_radius=ft.BorderRadius.all(8),
            content_padding=ft.Padding.symmetric(vertical=6, horizontal=10),
        )

        # Compatibilidad Flet: algunas versiones no aceptan on_change en el constructor,
        # pero sí permiten asignarlo después de crear el control.
        for _control in (
            self.dd_tipo_txt,
            self.txt_archivo,
            self.txt_satys_user,
            self.txt_satys_pass,
            self.txt_workers,
            self.sw_headless,
            self.txt_python,
            self.txt_script,
        ):
            self._safe_bind(_control, "on_change", self._on_form_change)

        self.command_preview = ft.TextField(
            label="Comando que se ejecutará",
            value="",
            read_only=True,
            multiline=True,
            min_lines=2,
            max_lines=4,
            expand=True,
            text_size=12,
            border_color=BORDER_COLOR,
            focused_border_color=BORDER_COLOR,
            border_radius=ft.BorderRadius.all(10),
            color=TEXT_GRAY,
            content_padding=ft.Padding.all(12),
        )

        # Estado / acciones
        self.status_icon = ft.Icon(ft.Icons.CIRCLE, size=12, color=TEXT_MUTED)
        self.status_label = ft.Text("Listo", size=13, color=TEXT_MUTED)
        self.counter_label = ft.Text("0 elementos detectados", size=12, color=TEXT_MUTED)
        self.progress = ft.ProgressBar(value=0, visible=False, color=TEAL_PROGRESS, bgcolor=DIVIDER_COLOR)

        self.btn_iniciar = ft.ElevatedButton(
            "Iniciar procesamiento",
            icon=ft.Icons.PLAY_ARROW,
            height=44,
            style=ft.ButtonStyle(
                bgcolor=TEAL_PRIMARY,
                color="white",
                shape=ft.RoundedRectangleBorder(radius=10),
                padding=ft.Padding.symmetric(horizontal=18, vertical=8),
            ),
            on_click=self._start_process,
        )
        self.btn_detener = ft.OutlinedButton(
            "Detener",
            icon=ft.Icons.STOP,
            height=44,
            visible=False,
            style=ft.ButtonStyle(
                color=RED_ERR,
                side=ft.BorderSide(1, RED_ERR),
                shape=ft.RoundedRectangleBorder(radius=10),
                padding=ft.Padding.symmetric(horizontal=16, vertical=8),
            ),
            on_click=self._stop_process,
        )

        # Log
        self.log_view = ft.ListView(
            expand=True,
            auto_scroll=True,
            spacing=2,
            padding=ft.Padding.only(left=14, right=12, top=12, bottom=12),
        )

        # Salidas: etiquetas actualizables
        self.out_excel_control_status = ft.Text("Pendiente de verificar", size=12, color=TEXT_MUTED)
        self.out_excel_consolidado_status = ft.Text("Pendiente de verificar", size=12, color=TEXT_MUTED)
        self.out_output_status = ft.Text("Pendiente de verificar", size=12, color=TEXT_MUTED)
        self.out_descargas_status = ft.Text("Pendiente de verificar", size=12, color=TEXT_MUTED)

        self.toast = ft.Container(
            visible=False,
            bgcolor=TEXT_DARK,
            border_radius=ft.BorderRadius.all(10),
            padding=ft.Padding.symmetric(horizontal=16, vertical=10),
            content=ft.Text("", color="white", size=12),
        )

        self.header_title = ft.Text("Procesamiento SATyS", size=26, weight=ft.FontWeight.BOLD, color=TEXT_DARK)
        self.screen_container = ft.Container(expand=True)
        self.header_row = self._build_header()

        self._update_command_preview()
        self._refresh_outputs()

        self.page.on_keyboard_event = self._handle_keyboard

    # ════════════════════════════════════════════════════
    #  CONTROLES VISUALES BASE
    # ════════════════════════════════════════════════════

    def _show_toast(self, message: str, seconds: float = 2.0) -> None:
        try:
            self.toast.content.value = message
            self.toast.visible = True
            self.page.update()

            def hide():
                time.sleep(seconds)
                self.toast.visible = False
                try:
                    self.page.update()
                except Exception:
                    pass

            threading.Thread(target=hide, daemon=True).start()
        except Exception:
            pass

    def _card(self, title: str, subtitle: str | None, content: ft.Control, icon=None) -> ft.Container:
        header_controls = []
        if icon is not None:
            header_controls.append(ft.Icon(icon, size=18, color=TEAL_PRIMARY))
        header_controls.extend([
            ft.Column(
                spacing=2,
                expand=True,
                controls=[
                    ft.Text(title, size=15, color=TEXT_DARK, weight=ft.FontWeight.W_700),
                    ft.Text(subtitle or "", size=12, color=TEXT_MUTED, visible=bool(subtitle)),
                ],
            )
        ])
        return ft.Container(
            bgcolor=CARD_BG,
            border_radius=ft.BorderRadius.all(16),
            border=ft.Border.all(1, BORDER_COLOR),
            padding=ft.Padding.all(18),
            content=ft.Column(
                spacing=14,
                controls=[
                    ft.Row(alignment=ft.MainAxisAlignment.START, controls=header_controls),
                    content,
                ],
            ),
        )

    def _small_button(self, label: str, icon, on_click) -> ft.Control:
        return ft.OutlinedButton(
            label,
            icon=icon,
            height=36,
            style=ft.ButtonStyle(
                color=TEAL_PRIMARY,
                side=ft.BorderSide(1, TEAL_PRIMARY),
                shape=ft.RoundedRectangleBorder(radius=9),
                padding=ft.Padding.symmetric(horizontal=12, vertical=6),
            ),
            on_click=on_click,
        )

    def _pill(self, text: str, icon, color: str = TEAL_PRIMARY) -> ft.Control:
        return ft.Container(
            bgcolor="#EDF6F7",
            border_radius=ft.BorderRadius.all(999),
            padding=ft.Padding.symmetric(horizontal=12, vertical=7),
            content=ft.Row(
                spacing=6,
                tight=True,
                controls=[ft.Icon(icon, size=14, color=color), ft.Text(text, size=12, color=color, weight=ft.FontWeight.W_600)],
            ),
        )

    # ════════════════════════════════════════════════════
    #  LAYOUT GENERAL
    # ════════════════════════════════════════════════════

    def _build_sidebar(self) -> ft.Control:
        if not self._sidebar_visible:
            return ft.Container(
                width=56,
                bgcolor=SIDEBAR_BG,
                padding=ft.Padding.only(top=26),
                content=ft.Column(
                    horizontal_alignment=ft.CrossAxisAlignment.CENTER,
                    controls=[
                        ft.IconButton(
                            icon=ft.Icons.MENU,
                            icon_color=TEAL_DARK,
                            tooltip="Mostrar menú",
                            on_click=self._toggle_sidebar,
                        )
                    ],
                ),
            )

        nav_controls = []
        for label, icon_outline, icon_filled in NAV_ITEMS:
            selected = label == self.active_nav
            nav_controls.append(
                ft.Container(
                    height=48,
                    border_radius=ft.BorderRadius.all(10),
                    bgcolor=SIDEBAR_SEL_BG if selected else None,
                    padding=ft.Padding.symmetric(horizontal=14, vertical=6),
                    on_click=lambda e, l=label: self._select_nav(l),
                    content=ft.Row(
                        spacing=12,
                        controls=[
                            ft.Icon(icon_filled if selected else icon_outline, size=22, color=TEAL_DARK if selected else TEXT_GRAY),
                            ft.Text(label, size=14, color=TEAL_DARK if selected else TEXT_GRAY, weight=ft.FontWeight.W_600 if selected else ft.FontWeight.NORMAL),
                        ],
                    ),
                )
            )

        return ft.Container(
            width=260,
            bgcolor=SIDEBAR_BG,
            padding=ft.Padding.only(left=16, right=12, top=24, bottom=16),
            content=ft.Column(
                spacing=22,
                expand=True,
                controls=[
                    ft.Row(
                        alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                        controls=[
                            ft.Column(spacing=2, controls=[
                                ft.Text("AUTOMATIZACIÓN", size=11, color=TEXT_MUTED, weight=ft.FontWeight.W_700),
                                ft.Text("SATyS CRT", size=18, color=TEAL_DARK, weight=ft.FontWeight.BOLD),
                            ]),
                            ft.IconButton(icon=ft.Icons.CHEVRON_LEFT, icon_color=TEXT_GRAY, tooltip="Contraer menú", on_click=self._toggle_sidebar),
                        ],
                    ),
                    ft.Column(spacing=8, controls=nav_controls),
                    ft.Container(expand=True),
                    ft.Text("© CRT 2025", size=11, color=TEXT_MUTED),
                ],
            ),
        )

    def _build_header(self) -> ft.Control:
        return ft.Row(
            alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
            vertical_alignment=ft.CrossAxisAlignment.START,
            controls=[
                ft.Column(
                    spacing=3,
                    controls=[
                        ft.Row(spacing=10, controls=[
                            ft.Column(spacing=0, controls=[
                                ft.Text("SATyS — Descarga, Excel y Organización", size=13, color=TEXT_GRAY),
                                self.header_title,
                            ]),
                        ]),
                    ],
                ),
                ft.Row(
                    spacing=10,
                    controls=[
                        self._small_button("descargas/", ft.Icons.FOLDER, lambda e: _abrir_ruta_al_frente(DESCARGAS_DIR)),
                        self._small_button("output/", ft.Icons.FOLDER_COPY, lambda e: _abrir_ruta_al_frente(OUTPUT_DIR)),
                        self._small_button("Excel", ft.Icons.TABLE_CHART, lambda e: _abrir_ruta_al_frente(EXCEL_CONTROL, seleccionar_archivo=True)),
                    ],
                ),
            ],
        )

    def _build_active_screen(self) -> ft.Control:
        """Construye la pantalla activa sin regresar siempre a Procesar.

        Corrección importante: antes, build() siempre hacía:
            self.screen_container.content = self._build_screen_procesar()
        Por eso al presionar Salidas, Historial o Configuración solo cambiaba
        el título, pero el contenido volvía a Procesar durante _rebuild().
        """
        if self.active_nav == "Resumen":
            return self._build_screen_resumen()
        if self.active_nav == "Salidas":
            return self._build_screen_salidas()
        if self.active_nav == "Historial":
            return self._build_screen_historial()
        if self.active_nav == "Configuración":
            return self._build_screen_config()
        return self._get_screen_procesar()

    def build(self) -> ft.Control:
        # Mantener el contenido sincronizado con la opción seleccionada del menú.
        self.screen_container.content = self._build_active_screen()
        main_area = ft.Container(
            expand=True,
            bgcolor=PAGE_BG,
            padding=ft.Padding.only(left=28, right=28, top=22, bottom=14),
            content=ft.Column(
                spacing=16,
                expand=True,
                controls=[
                    self.header_row,
                    self.screen_container,
                    ft.Text(
                        "Optimizado para Windows · Las ventanas nuevas se abren al frente · Comisión Reguladora de Telecomunicaciones",
                        size=11,
                        color=TEXT_MUTED,
                    ),
                ],
            ),
        )
        return ft.Stack(
            expand=True,
            controls=[
                ft.Row(expand=True, spacing=0, controls=[self._build_sidebar(), main_area]),
                ft.Container(top=16, right=16, content=self.toast),
            ],
        )

    def _rebuild(self) -> None:
        self.page.controls.clear()
        self.page.add(self.build())
        try:
            self.page.update()
        except Exception:
            pass

    def _refresh_summary_if_visible(self, force: bool = False) -> None:
        """Actualiza Resultados automáticamente sin mover la posición de scroll."""
        if self.active_nav not in ("Procesar", "Resumen"):
            return

        now = time.time()
        if not force and (now - self._last_summary_refresh_ts) < self._summary_refresh_interval:
            return

        self._last_summary_refresh_ts = now
        try:
            if self.active_nav == "Procesar":
                # Antes se reconstruía toda la pantalla Procesar cada segundo con:
                # self.screen_container.content = self._build_active_screen()
                # Eso hacía que el scroll regresara arriba. Ahora solo se cambia
                # el contenido del slot de Resultados debajo del log.
                self._set_procesar_summary_content()
            else:
                # En la pestaña Resumen sí se puede reconstruir esa vista completa.
                self.screen_container.content = self._build_screen_resumen()
            self.page.update()
        except Exception:
            pass

    def _prepare_current_run_summary(self) -> None:
        """Marca la corrida actual para que Resultados no use JSON viejos."""
        self._run_started_at = time.time()
        self._run_input_kind = self.dd_tipo_txt.value or "registros"
        self._run_input_items = self._read_input_items()
        self._last_summary_refresh_ts = 0.0

    def _start_summary_auto_refresh(self) -> None:
        if self._summary_refresh_thread_active:
            return
        self._summary_refresh_thread_active = True

        def loop() -> None:
            try:
                while self._running:
                    time.sleep(self._summary_refresh_interval)
                    self._refresh_summary_if_visible(force=True)
            finally:
                self._summary_refresh_thread_active = False

        threading.Thread(target=loop, daemon=True).start()

    # ════════════════════════════════════════════════════
    #  PANTALLA: PROCESAR
    # ════════════════════════════════════════════════════

    def _set_procesar_summary_content(self) -> None:
        """Actualiza solo el bloque Resultados incrustado, sin reconstruir toda la pantalla."""
        try:
            self._procesar_summary_slot.content = self._build_resumen_bajo_log()
        except Exception:
            pass

    def _get_screen_procesar(self) -> ft.Control:
        """Devuelve una pantalla Procesar persistente para conservar el scroll."""
        if self._procesar_screen is None:
            self._procesar_screen = self._build_screen_procesar()
        return self._procesar_screen

    def _build_screen_procesar(self) -> ft.Control:
        entrada_card = self._card(
            "1. Archivo de entrada",
            "Selecciona el TXT y dile al sistema qué contiene.",
            ft.Column(
                spacing=12,
                controls=[
                    ft.Row(
                        spacing=12,
                        vertical_alignment=ft.CrossAxisAlignment.CENTER,
                        controls=[
                            self.dd_tipo_txt,
                            self.txt_archivo,
                            ft.ElevatedButton(
                                "Examinar",
                                icon=ft.Icons.UPLOAD_FILE,
                                height=48,
                                style=ft.ButtonStyle(
                                    bgcolor=TEAL_PRIMARY,
                                    color="white",
                                    shape=ft.RoundedRectangleBorder(radius=10),
                                    padding=ft.Padding.symmetric(horizontal=16, vertical=8),
                                ),
                                on_click=self._choose_txt,
                            ),
                        ],
                    ),
                    ft.Container(
                        bgcolor=SOFT_CARD_BG,
                        border_radius=ft.BorderRadius.all(12),
                        border=ft.Border.all(1, BORDER_COLOR),
                        padding=ft.Padding.all(12),
                        content=ft.Column(
                            spacing=8,
                            controls=[
                                ft.Row(
                                    spacing=8,
                                    controls=[
                                        ft.Icon(ft.Icons.LOCK_OUTLINE, size=16, color=TEAL_PRIMARY),
                                        ft.Text("Credenciales de ingreso a SATyS", size=13, color=TEXT_DARK, weight=ft.FontWeight.W_700),
                                        ft.Text("se leen automáticamente y también se pueden escribir manualmente", size=11, color=TEXT_MUTED),
                                    ],
                                ),
                                ft.Row(
                                    spacing=10,
                                    vertical_alignment=ft.CrossAxisAlignment.CENTER,
                                    controls=[
                                        self.txt_satys_user,
                                        self.txt_satys_pass,
                                        self._small_button("Recargar", ft.Icons.REFRESH, self._reload_credentials),
                                        self._small_button("Guardar", ft.Icons.SAVE, self._save_credentials_to_file),
                                    ],
                                ),
                                self.cred_status,
                            ],
                        ),
                    ),
                    ft.Row(
                        spacing=8,
                        controls=[
                            self._pill("TXT como única entrada", ft.Icons.DESCRIPTION),
                            self._pill("Registros CRT o folios SATyS", ft.Icons.RULE),
                            self._pill("Rutas con espacios permitidas", ft.Icons.CHECK_CIRCLE),
                        ],
                    ),
                ],
            ),
            icon=ft.Icons.INPUT,
        )

        ejecucion_card = self._card(
            "2. Ejecución",
            "Parámetros normales para producción: 6 ventanas y modo rápido activado.",
            ft.Column(
                spacing=12,
                controls=[
                    ft.Row(
                        spacing=18,
                        vertical_alignment=ft.CrossAxisAlignment.CENTER,
                        controls=[
                            self.txt_workers,
                            self.sw_headless,
                            ft.Container(expand=True),
                            self.btn_iniciar,
                            self.btn_detener,
                        ],
                    ),
                    ft.Row(controls=[self.command_preview]),
                    ft.Row(
                        alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                        controls=[
                            ft.Row(spacing=8, controls=[self.status_icon, self.status_label, self.counter_label]),
                            ft.Text("F5 inicia · Esc detiene", size=12, color=TEXT_MUTED),
                        ],
                    ),
                    self.progress,
                ],
            ),
            icon=ft.Icons.ROCKET_LAUNCH_OUTLINED,
        )

        log_card = ft.Container(
            bgcolor=CARD_BG,
            border_radius=ft.BorderRadius.all(16),
            border=ft.Border.all(1, BORDER_COLOR),
            padding=ft.Padding.all(0),
            height=230,
            content=ft.Column(
                spacing=0,
                expand=True,
                controls=[
                    ft.Container(
                        height=56,
                        padding=ft.Padding.symmetric(horizontal=18, vertical=10),
                        border=ft.Border.only(bottom=ft.BorderSide(1, BORDER_COLOR)),
                        content=ft.Row(
                            alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                            controls=[
                                ft.Row(spacing=10, controls=[
                                    ft.Icon(ft.Icons.TERMINAL, size=18, color=TEXT_GRAY),
                                    ft.Text("Log del proceso", size=14, color=TEXT_DARK, weight=ft.FontWeight.W_700),
                                ]),
                                ft.Row(spacing=8, controls=[
                                    ft.TextButton("Copiar log", icon=ft.Icons.CONTENT_COPY, on_click=self._copy_log),
                                    ft.TextButton("Limpiar log", icon=ft.Icons.DELETE_OUTLINE, on_click=self._clear_log),
                                ]),
                            ],
                        ),
                    ),
                    ft.Container(
                        expand=True,
                        bgcolor=LOG_BG,
                        border_radius=ft.BorderRadius.all(16),
                        content=self.log_view,
                    ),
                ],
            ),
        )

        self._set_procesar_summary_content()
        resumen_bajo_log = self._procesar_summary_slot

        return ft.Column(
            expand=True,
            spacing=16,
            scroll=ft.ScrollMode.AUTO,
            controls=[
                entrada_card,
                ejecucion_card,
                log_card,
                resumen_bajo_log,
            ],
        )

    def _build_resumen_bajo_log(self) -> ft.Control:
        """Resumen embebido debajo del Log del proceso.

        Reutiliza la misma pantalla de Resumen, pero sin scroll/expand propios
        para que pueda vivir dentro de la pantalla Procesar.
        """
        resumen = self._build_screen_resumen()
        try:
            resumen.expand = False
        except Exception:
            pass
        try:
            resumen.scroll = None
        except Exception:
            pass
        return resumen


    # ════════════════════════════════════════════════════
    #  PANTALLA: RESULTADOS
    # ════════════════════════════════════════════════════

    def _read_json_safe(self, path: Path) -> dict:
        try:
            if path.exists():
                return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            pass
        return {}

    def _project_root(self) -> Path:
        """Raíz real donde corre main_procesar.py / Parte1_descarga.py."""
        try:
            script_path = Path((self.txt_script.value or DEFAULT_SCRIPT).strip().strip('"'))
            if script_path.exists() and script_path.is_file():
                return script_path.resolve().parent
        except Exception:
            pass
        return Path.cwd()

    def _descargas_dir_actual(self) -> Path:
        return self._project_root() / "descargas"

    def _summary_candidate_paths(self) -> list[Path]:
        base = self._descargas_dir_actual()
        kind = self._run_input_kind if self._running and self._run_input_kind else (self.dd_tipo_txt.value or "registros")
        # En modo registros el script genera procesamiento_log_registros.json;
        # en modo folios genera procesamiento_log.json. Ambos se buscan siempre
        # para cubrir el caso en que el usuario cambia de modo sin reiniciar.
        return [
            base / "procesamiento_log_registros.json",
            base / "procesamiento_log.json",
            base / "descarga_log.json",
            base / "resumen_global.json",
        ]

    def _read_input_items(self) -> list[str]:
        path_txt = Path((self.txt_archivo.value or "").strip().strip('"'))
        if not path_txt.exists() or not path_txt.is_file():
            return []
        items: list[str] = []
        try:
            with open(path_txt, "r", encoding="utf-8", errors="ignore") as f:
                for line in f:
                    line = line.strip()
                    if not line or line.startswith("#"):
                        continue
                    items.append(line)
        except Exception:
            return []
        return list(dict.fromkeys(items))

    def _digits_tail(self, value: str) -> str:
        import re as _re
        m = _re.search(r"(\d+)\s*$", str(value or ""))
        if not m:
            return str(value or "").strip().upper()
        try:
            return str(int(m.group(1)))
        except Exception:
            return m.group(1).lstrip("0") or "0"

    def _current_input_keys(self) -> set[str]:
        items = self._run_input_items if self._running and self._run_input_items else self._read_input_items()
        kind = self._run_input_kind if self._running and self._run_input_kind else (self.dd_tipo_txt.value or "registros")
        keys: set[str] = set()
        for item in items:
            raw = str(item).strip()
            if not raw:
                continue
            keys.add(raw.upper())
            if kind == "folios":
                keys.add(self._digits_tail(raw))
        return keys

    def _keys_for_result(self, r: dict) -> set[str]:
        keys: set[str] = set()
        kind = self._run_input_kind if self._running and self._run_input_kind else (self.dd_tipo_txt.value or "registros")
        fields = [
            # folio_id contiene el número CRT (ej. CRT26-013461) en procesamiento_log_registros.json
            r.get("folio_id"), r.get("registro"), r.get("numero_registro"), r.get("folio_raw"),
            r.get("folio"), r.get("folio_satys"), r.get("id"),
        ]
        for val in fields:
            if val is None:
                continue
            raw = str(val).strip()
            if not raw:
                continue
            keys.add(raw.upper())
            if kind == "folios":
                keys.add(self._digits_tail(raw))
        return keys

    def _input_match_count(self, resultados: list[dict]) -> int:
        input_keys = self._current_input_keys()
        if not input_keys:
            return 0
        matched = set()
        for r in resultados:
            inter = input_keys & self._keys_for_result(r)
            if inter:
                matched.update(inter)
        return len(matched)

    def _filter_results_to_current_input(self, resultados: list[dict]) -> list[dict]:
        input_keys = self._current_input_keys()
        if not input_keys:
            return resultados
        filtrados = [r for r in resultados if input_keys & self._keys_for_result(r)]
        return filtrados

    def _group_file_results(self, rows: list[dict]) -> list[dict]:
        """Convierte descarga_log.json, que viene por archivo, a una fila por folio/registro."""
        grouped: dict[str, dict] = {}
        kind = self._run_input_kind if self._running and self._run_input_kind else (self.dd_tipo_txt.value or "registros")
        for r in rows:
            registro = str(r.get("registro") or r.get("numero_registro") or "").strip()
            folio = str(r.get("folio") or r.get("folio_satys") or r.get("id") or "").strip()
            key = registro if kind == "registros" and registro else folio or registro or "?"
            g = grouped.setdefault(key, {
                "folio": folio or key,
                "registro": registro,
                "archivos_ok": 0,
                "archivos_error": 0,
                "archivos_descargados": [],
                "carpeta": r.get("carpeta") or str(self._descargas_dir_actual() / key),
                "estado": "PENDIENTE",
            })
            if registro and not g.get("registro"):
                g["registro"] = registro
            if folio and (not g.get("folio") or g.get("folio") == key):
                g["folio"] = folio
            if r.get("carpeta"):
                g["carpeta"] = r.get("carpeta")
            g["archivos_descargados"].append(r)
            texto = " ".join(str(r.get(k, "")) for k in ("archivo", "error", "tipo", "ruta", "mensaje"))
            if "FOLIO_NO_ENCONTRADO" in texto.upper() or "REGISTRO_NO_ENCONTRADO" in texto.upper():
                g["no_encontrado"] = True
                g["estado"] = "NO_ENCONTRADO"
            if r.get("ok"):
                g["archivos_ok"] += 1
            else:
                g["archivos_error"] += 1
                if not g.get("no_encontrado"):
                    g["estado"] = "INCOMPLETO"
                    g["error"] = r.get("error") or r.get("tipo") or r.get("archivo") or "Revisión necesaria"

        out = []
        for g in grouped.values():
            if g.get("no_encontrado"):
                g["estado"] = "NO_ENCONTRADO"
            elif g.get("archivos_ok", 0) > 0 and g.get("archivos_error", 0) == 0:
                g["estado"] = "OK"
                g.setdefault("organizado_ok", True)
                g.setdefault("excel_ok", True)
                g.setdefault("rpc_ok", True)
            elif g.get("archivos_ok", 0) > 0:
                g["estado"] = "INCOMPLETO"
            else:
                g["estado"] = g.get("estado") or "INCOMPLETO"
            out.append(g)
        return out

    def _summary_results(self, data: dict) -> list[dict]:
        """Normaliza resultados de procesamiento_log.json, descarga_log.json o resumen_global.json."""
        if isinstance(data.get("resultados"), list):
            rows = data.get("resultados") or []
            # descarga_log.json guarda una fila por archivo; se agrupa por folio/registro.
            if any(isinstance(r, dict) and ("ok" in r and "archivo" in r) for r in rows):
                return self._group_file_results([r for r in rows if isinstance(r, dict)])
            return [r for r in rows if isinstance(r, dict)]
        if isinstance(data.get("detalle_folios"), list):
            # resumen_global.json de Parte1 usa detalle_folios. Lo adaptamos a la UI.
            out = []
            for r in data.get("detalle_folios") or []:
                if not isinstance(r, dict):
                    continue
                rr = dict(r)
                estado = str(rr.get("estado", "")).upper()
                rr.setdefault("organizado_ok", estado == "OK")
                rr.setdefault("excel_ok", estado == "OK")
                rr.setdefault("rpc_ok", estado == "OK")
                rr.setdefault("pdf_encontrado", estado != "NO_ENCONTRADO")
                out.append(rr)
            return out
        return []

    def _build_live_summary_from_current_run(self) -> tuple[dict, str]:
        """Lee avances de la corrida actual aunque todavía no exista el JSON final."""
        if not self._running and not self._run_started_at:
            return {}, ""

        started = (self._run_started_at or 0) - 2.0
        base = self._descargas_dir_actual()
        items = self._run_input_items or self._read_input_items()
        kind = self._run_input_kind or (self.dd_tipo_txt.value or "registros")
        rows: list[dict] = []
        seen_paths: set[str] = set()

        for item in items:
            safe = str(item).strip()
            if not safe:
                continue
            # Normalmente queda en descargas/<folio> o descargas/<registro>.
            patterns = [base / safe / "metadata_completo.json"]
            try:
                patterns.extend(base.glob(f"{safe}_*/**/metadata_completo.json"))
            except Exception:
                pass
            for mp in patterns:
                try:
                    mp = Path(mp)
                    if not mp.exists() or str(mp) in seen_paths:
                        continue
                    if mp.stat().st_mtime < started:
                        continue
                    seen_paths.add(str(mp))
                    meta = json.loads(mp.read_text(encoding="utf-8"))
                    if not isinstance(meta, dict):
                        continue
                    meta_satys = meta.get("metadatos_satys") or {}
                    row = {
                        "folio": meta.get("folio") or item,
                        "registro": meta_satys.get("registro") or (item if kind == "registros" else ""),
                        "folio_raw": meta.get("folio_raw") or item,
                        "estado": meta.get("estado") or "INCOMPLETO",
                        "archivos_ok": meta.get("total_archivos_ok", 0),
                        "archivos_error": meta.get("total_archivos_error", 0),
                        "carpeta": str(mp.parent),
                        "archivos_descargados": meta.get("archivos", []),
                    }
                    estado = str(row.get("estado", "")).upper()
                    if estado == "PARCIAL" or estado == "SIN_ARCHIVOS":
                        row["estado"] = "INCOMPLETO"
                    if estado == "OK":
                        row.setdefault("rpc_ok", True)
                        row.setdefault("organizado_ok", True)
                        row.setdefault("excel_ok", True)
                    rows.append(row)
                except Exception:
                    pass

        # Completar avance desde líneas del log si todavía no hay metadata.
        import re as _re
        existing_keys = set()
        for r in rows:
            existing_keys.update(self._keys_for_result(r))
        for line in self._log_lines[-500:]:
            m = _re.search(r"Registro\s+([^\s:]+)\s+completado:\s+(\d+)\s+OK\s*/\s*(\d+)\s+total", line, _re.I)
            if not m:
                m = _re.search(r"Folio\s+([^\s:]+)\s+completado:\s+(\d+)\s+OK\s*/\s*(\d+)\s+total", line, _re.I)
            if m:
                num, ok_s, total_s = m.group(1), m.group(2), m.group(3)
                probe = {"registro" if kind == "registros" else "folio": num}
                if existing_keys & self._keys_for_result(probe):
                    continue
                ok_i, total_i = int(ok_s), int(total_s)
                rows.append({
                    "registro" if kind == "registros" else "folio": num,
                    "estado": "OK" if total_i > 0 and ok_i == total_i else "INCOMPLETO",
                    "archivos_ok": ok_i,
                    "archivos_error": max(total_i - ok_i, 0),
                    "carpeta": str(base / num),
                    "rpc_ok": ok_i == total_i and total_i > 0,
                    "organizado_ok": ok_i == total_i and total_i > 0,
                    "excel_ok": ok_i == total_i and total_i > 0,
                })
                existing_keys.update(self._keys_for_result(rows[-1]))

            m2 = _re.search(r"(REGISTRO|FOLIO).*?NO[_\s-]?ENCONTRADO.*?([A-Z]{3}\d{2}-\d+|\d+)", line, _re.I)
            if m2:
                num = m2.group(2)
                probe = {"registro" if kind == "registros" else "folio": num}
                if existing_keys & self._keys_for_result(probe):
                    continue
                rows.append({
                    "registro" if kind == "registros" else "folio": num,
                    "estado": "NO_ENCONTRADO",
                    "no_encontrado": True,
                    "error": f"{m2.group(1).upper()}_NO_ENCONTRADO",
                    "carpeta": str(base / num),
                })
                existing_keys.update(self._keys_for_result(rows[-1]))

        rows = self._filter_results_to_current_input(rows)
        if rows:
            return {"resultados": rows, "fecha": datetime.now().isoformat(), "en_vivo": True}, "avance en vivo de la ejecución actual"
        return {}, ""

    def _load_summary_source(self) -> tuple[dict, str]:
        """Carga Resultados de la ejecución actual.
        - En modo 'registros' usa procesamiento_log_registros.json directamente.
        - En modo 'folios' usa procesamiento_log.json directamente.
        - Si el archivo primario no existe o no tiene matches, cae al sistema de candidatos.
        """
        base = self._descargas_dir_actual()
        run_started = self._run_started_at
        input_keys = self._current_input_keys()
        kind = self._run_input_kind if self._running and self._run_input_kind else (self.dd_tipo_txt.value or "registros")

        # --- Intento 1: archivo primario según el modo actual ---
        primary_name = "procesamiento_log_registros.json" if kind == "registros" else "procesamiento_log.json"
        primary_path = base / primary_name
        if primary_path.exists():
            try:
                primary_mtime = primary_path.stat().st_mtime
                # Si estamos en mitad de una corrida, sólo aceptar archivos de esta corrida.
                if run_started and primary_mtime < run_started - 2.0:
                    pass  # archivo viejo de corrida anterior, ignorar
                else:
                    primary_data = self._read_json_safe(primary_path)
                    if primary_data:
                        primary_rows = self._summary_results(primary_data)
                        # Si no hay keys activos o el archivo tiene matches, usarlo directamente.
                        if not input_keys or self._input_match_count(primary_rows) > 0:
                            if not self._running:
                                return primary_data, str(primary_path)
                            # Durante la corrida, mezclar con datos en vivo si los hay.
                            live_data, live_source = self._build_live_summary_from_current_run()
                            if live_data:
                                live_rows = self._summary_results(live_data)
                                merged: dict[str, dict] = {}
                                for row in primary_rows + live_rows:
                                    ident = str(row.get("folio_id") or row.get("carpeta") or
                                                self._identifier_for_csv(row) or self._result_folio(row)).strip()
                                    merged[ident] = row
                                return {"resultados": list(merged.values()), "fecha": datetime.now().isoformat(), "en_vivo": True}, f"{primary_path} + {live_source}"
                            return primary_data, str(primary_path)
            except Exception:
                pass

        # --- Intento 2: sistema de candidatos (fallback) ---
        candidates = []
        for path in self._summary_candidate_paths():
            data = self._read_json_safe(path)
            if not data:
                continue
            try:
                mtime = path.stat().st_mtime
            except Exception:
                mtime = 0.0
            resultados = self._summary_results(data)
            match_count = self._input_match_count(resultados)
            candidates.append({
                "path": path,
                "data": data,
                "mtime": mtime,
                "resultados": resultados,
                "match_count": match_count,
            })

        if run_started:
            candidates = [c for c in candidates if c["mtime"] >= run_started - 2.0]

        chosen = None
        if candidates:
            if input_keys:
                with_matches = [c for c in candidates if c["match_count"] > 0]
                if with_matches:
                    chosen = max(with_matches, key=lambda c: (c["match_count"], c["mtime"]))
            else:
                chosen = max(candidates, key=lambda c: c["mtime"])

        if chosen and not self._running:
            live_data, live_source = None, ""
        else:
            live_data, live_source = self._build_live_summary_from_current_run()

        if live_data:
            live_rows = self._summary_results(live_data)
            if chosen:
                chosen_rows = self._filter_results_to_current_input(chosen["resultados"])
                merged2: dict[str, dict] = {}
                for row in chosen_rows + live_rows:
                    ident = str(row.get("folio_id") or row.get("carpeta") or self._identifier_for_csv(row) or self._result_folio(row)).strip()
                    merged2[ident] = row
                return {"resultados": list(merged2.values()), "fecha": datetime.now().isoformat(), "en_vivo": True}, f"{chosen['path']} + {live_source}"
            return live_data, live_source

        if chosen:
            rows = self._filter_results_to_current_input(chosen["resultados"])
            data = dict(chosen["data"])
            data["resultados"] = rows
            data.pop("detalle_folios", None)
            return data, str(chosen["path"])

        return {}, ""

    def _result_folio(self, r: dict) -> str:
        # En modo Registro, mostrar y copiar primero el número CRT; en modo Folio, mostrar el folio SATyS.
        kind = self._run_input_kind if self._running and self._run_input_kind else (self.dd_tipo_txt.value or "registros")
        if kind == "registros":
            # folio_id = n\u00famero CRT (CRT26-XXXXXX) en procesamiento_log_registros.json
            return str(r.get("folio_id") or r.get("registro") or r.get("numero_registro") or r.get("folio") or r.get("id") or "?")
        return str(r.get("folio") or r.get("folio_satys") or r.get("folio_id") or r.get("registro") or r.get("numero_registro") or r.get("id") or "?")

    def _result_folder(self, r: dict) -> Path:
        carpeta = r.get("carpeta") or r.get("carpeta_descarga") or r.get("output_dir") or r.get("sin_operador_dir")
        if carpeta:
            return Path(str(carpeta))
        return self._descargas_dir_actual() / self._result_folio(r)

    def _is_success_result(self, r: dict) -> bool:
        estado = str(r.get("estado", "")).upper()
        if estado == "OK":
            return True
        return bool(r.get("rpc_ok") and r.get("organizado_ok") and r.get("excel_ok"))

    def _is_duplicate_result(self, r: dict) -> bool:
        texto = " ".join(str(r.get(k, "")) for k in ("estado", "error", "mensaje", "nota", "tipo"))
        return bool(r.get("duplicado_rpc") or r.get("rpc_duplicado") or r.get("duplicado") or "DUPLIC" in texto.upper())

    def _is_low_match_result(self, r: dict) -> bool:
        if self._is_success_result(r) or self._is_duplicate_result(r):
            return False
        rpc_res = r.get("rpc_resultado") or {}
        if isinstance(rpc_res, dict) and rpc_res:
            score = float(rpc_res.get("score") or r.get("score_rpc") or 0)
            return score < 0.80 or not r.get("rpc_ok")
        return bool(r.get("coincidencia_baja") or r.get("rpc_bajo"))

    def _is_not_found_result(self, r: dict) -> bool:
        estado = str(r.get("estado", "")).upper()
        texto = " ".join(str(r.get(k, "")) for k in ("estado", "error", "mensaje", "archivo", "tipo", "ruta"))
        texto_u = texto.upper()
        return bool(
            estado == "NO_ENCONTRADO"
            or r.get("no_encontrado")
            or r.get("folio_no_encontrado")
            or r.get("registro_no_encontrado")
            or str(r.get("archivo", "")).upper() in ("FOLIO_NO_ENCONTRADO", "REGISTRO_NO_ENCONTRADO")
            or "NO_ENCONTRADO" in texto_u
            or "NO ENCONTRADO" in texto_u
            or "REGISTRO_NO_ENCONTRADO" in texto_u
        )

    def _is_incomplete_result(self, r: dict) -> bool:
        estado = str(r.get("estado", "")).upper()
        if estado == "INCOMPLETO":
            return True
        if self._is_success_result(r) or self._is_not_found_result(r):
            return False
        return bool(r.get("archivos_error") or r.get("pdf_encontrado") is False or r.get("organizado_ok") is False)

    def _score_text(self, r: dict) -> str:
        rpc_res = r.get("rpc_resultado") or {}
        score = r.get("score_rpc")
        if isinstance(rpc_res, dict):
            score = rpc_res.get("score", score)
        try:
            score_f = float(score or 0)
            if score_f <= 1:
                score_f *= 100
            return f"{score_f:.0f}%" if score_f else "—"
        except Exception:
            return "—"

    def _stat_col(self, title: str, value) -> ft.Control:
        return ft.Column(
            horizontal_alignment=ft.CrossAxisAlignment.CENTER,
            spacing=2,
            controls=[
                ft.Text(str(value), size=24, color="#FFF", weight=ft.FontWeight.W_800),
                ft.Text(title, size=11, color="#E0F7FA", weight=ft.FontWeight.W_600),
            ],
        )

    def _summary_chip(self, title: str, detail: str) -> ft.Control:
        return ft.Container(
            padding=ft.Padding.symmetric(horizontal=10, vertical=7),
            border_radius=ft.BorderRadius.all(7),
            border=ft.Border.all(1, BORDER_COLOR),
            bgcolor=PAGE_BG,
            content=ft.Column(
                spacing=2,
                controls=[
                    ft.Text(title, size=12, color=TEXT_DARK, weight=ft.FontWeight.W_700),
                    ft.Text(detail or "—", size=11, color=TEXT_GRAY),
                ],
            ),
        )

    def _identifier_for_csv(self, r: dict) -> str:
        return str(
            r.get("registro")
            or r.get("numero_registro")
            or r.get("folio")
            or r.get("folio_satys")
            or r.get("id")
            or ""
        ).strip()

    def _summary_csv_for_items(self, items: list[dict]) -> str:
        buff = io.StringIO()
        writer = csv.writer(buff, lineterminator="\n")
        writer.writerow(["numero"])
        seen = set()
        for r in items:
            numero = self._identifier_for_csv(r)
            if not numero or numero == "?" or numero in seen:
                continue
            seen.add(numero)
            writer.writerow([numero])
        return buff.getvalue().strip()

    def _copy_summary_items_csv(self, titulo: str, items: list[dict]) -> None:
        csv_text = self._summary_csv_for_items(items)
        try:
            self.page.set_clipboard(csv_text)
            self._show_toast(f"CSV copiado: {titulo} ({max(len(csv_text.splitlines()) - 1, 0)} número(s))")
        except Exception:
            self._show_toast("No se pudo copiar el CSV")

    def _copy_csv_button(self, titulo: str, items: list[dict]) -> ft.Control:
        return ft.TextButton(
            "Copiar CSV",
            icon=ft.Icons.CONTENT_COPY,
            style=ft.ButtonStyle(
                color=TEAL_PRIMARY,
                padding=ft.Padding.symmetric(horizontal=8, vertical=0),
            ),
            on_click=lambda e, t=titulo, it=items: self._copy_summary_items_csv(t, it),
        )

    def _resumen_seccion(self, titulo: str, items: list[dict], color: str, detalle_fn) -> ft.Control:
        chips = []
        for r in items:
            chips.append(self._summary_chip(self._result_folio(r), detalle_fn(r)))
        return ft.Container(
            padding=ft.Padding.all(14),
            border_radius=ft.BorderRadius.all(10),
            border=ft.Border.all(1, BORDER_COLOR),
            bgcolor=CARD_BG,
            content=ft.Column(
                spacing=10,
                controls=[
                    ft.Row(
                        spacing=8,
                        vertical_alignment=ft.CrossAxisAlignment.CENTER,
                        controls=[
                            ft.Container(width=4, height=20, bgcolor=color, border_radius=ft.BorderRadius.all(2)),
                            ft.Text(titulo, size=13, color=TEXT_DARK, weight=ft.FontWeight.W_700),
                            ft.Container(
                                padding=ft.Padding.symmetric(horizontal=8, vertical=2),
                                bgcolor=color,
                                border_radius=ft.BorderRadius.all(10),
                                content=ft.Text(str(len(items)), size=11, color="#FFF", weight=ft.FontWeight.W_700),
                            ),
                            ft.Container(expand=True),
                            self._copy_csv_button(titulo, items),
                        ],
                    ),
                    ft.Row(wrap=True, spacing=8, run_spacing=8, controls=chips)
                    if chips else ft.Text("Ninguno.", size=12, color=TEXT_MUTED, italic=True),
                ],
            ),
        )

    def _resumen_seccion_sin_operador(self, items: list[dict]) -> ft.Control:
        chips = []
        for r in items:
            folio = self._result_folio(r)
            id_sol = r.get("id_solicitante") or r.get("id_solicitante_satys") or "N/A"
            sin_op = r.get("sin_operador_dir") or str(OUTPUT_DIR / "_sin_operador" / folio)
            chips.append(
                ft.Container(
                    padding=ft.Padding.symmetric(horizontal=12, vertical=8),
                    border_radius=ft.BorderRadius.all(7),
                    border=ft.Border.all(1, BORDER_COLOR),
                    bgcolor=PAGE_BG,
                    content=ft.Column(
                        spacing=3,
                        controls=[
                            ft.Text(f"Folio: {folio}", size=12, color=TEXT_DARK, weight=ft.FontWeight.W_700),
                            ft.Text(f"id_solicitante={id_sol} — no encontrado en catálogo RPC", size=11, color=TEXT_GRAY),
                            ft.Row(
                                spacing=4,
                                controls=[
                                    ft.Icon(ft.Icons.ARROW_FORWARD, size=11, color=ORANGE_WARN),
                                    ft.Text(f"Mueve archivos desde: {sin_op}", size=10, color=ORANGE_WARN, italic=True),
                                ],
                            ),
                        ],
                    ),
                )
            )
        return ft.Container(
            padding=ft.Padding.all(14),
            border_radius=ft.BorderRadius.all(10),
            border=ft.Border.all(1, BORDER_COLOR),
            bgcolor=CARD_BG,
            content=ft.Column(
                spacing=10,
                controls=[
                    ft.Row(
                        spacing=8,
                        vertical_alignment=ft.CrossAxisAlignment.CENTER,
                        controls=[
                            ft.Container(width=4, height=20, bgcolor=ORANGE_WARN, border_radius=ft.BorderRadius.all(2)),
                            ft.Text("📁 SIN OPERADOR EN CATÁLOGO (revisión manual)", size=13, color=TEXT_DARK, weight=ft.FontWeight.W_700),
                            ft.Container(
                                padding=ft.Padding.symmetric(horizontal=8, vertical=2),
                                bgcolor=ORANGE_WARN,
                                border_radius=ft.BorderRadius.all(10),
                                content=ft.Text(str(len(items)), size=11, color="#FFF", weight=ft.FontWeight.W_700),
                            ),
                            ft.Container(expand=True),
                            self._copy_csv_button("SIN OPERADOR EN CATÁLOGO", items),
                        ],
                    ),
                    ft.Column(spacing=6, controls=chips) if chips else ft.Text("Ninguno.", size=12, color=TEXT_MUTED, italic=True),
                ],
            ),
        )

    def _metadata_from_folder(self, carpeta: Path) -> dict:
        meta = {}
        for name in ("metadata_satys.json", "metadata_tramite_nuevo.json", "metadata_completo.json"):
            mp = carpeta / name
            if mp.exists():
                try:
                    value = json.loads(mp.read_text(encoding="utf-8"))
                    if isinstance(value, dict):
                        meta.update(value)
                except Exception:
                    pass
        return meta

    def _metadata_card(self, r: dict) -> ft.Control | None:
        folio = self._result_folio(r)
        carpeta = self._result_folder(r)
        meta = self._metadata_from_folder(carpeta)
        if not meta and not r:
            return None

        nombre = meta.get("nombre_operador") or meta.get("razon_social") or r.get("nombre") or r.get("nombre_operador") or r.get("operador") or "—"
        asunto = meta.get("asunto") or r.get("asunto") or "—"
        id_sol = meta.get("id_solicitante") or r.get("id_solicitante") or "—"
        rep_leg = meta.get("representante_legal") or r.get("representante_legal") or "—"
        fecha_reg = meta.get("fecha_registro") or meta.get("fecha") or r.get("fecha") or "—"

        archivos = []
        if carpeta.exists():
            try:
                for f in carpeta.iterdir():
                    if f.is_file() and f.suffix.lower() not in {".json", ".txt"}:
                        archivos.append(f.name)
            except Exception:
                pass
        if not archivos:
            for key in ("archivos_descargados", "archivos", "files"):
                val = r.get(key)
                if isinstance(val, list):
                    for item in val:
                        if isinstance(item, dict):
                            name = item.get("nombre") or item.get("archivo") or item.get("filename")
                        else:
                            name = str(item)
                        if name:
                            archivos.append(name)

        score = self._score_text(r)
        badge_color = TEAL_PRIMARY if self._is_success_result(r) or r.get("rpc_ok") else RED_ERR if self._is_not_found_result(r) else ORANGE_WARN
        lista_archivos = []
        if archivos:
            lista_archivos.append(ft.Text("Archivos descargados:", size=11, color=TEXT_GRAY, weight=ft.FontWeight.W_600))
            for arch in archivos[:12]:
                ext = arch.lower()
                icono = ft.Icons.PICTURE_AS_PDF if ext.endswith(".pdf") else ft.Icons.TABLE_CHART if ext.endswith((".xlsx", ".xls", ".csv")) else ft.Icons.INSERT_DRIVE_FILE
                color = RED_ERR if ext.endswith(".pdf") else GREEN_OK if ext.endswith((".xlsx", ".xls", ".csv")) else TEXT_GRAY
                lista_archivos.append(
                    ft.Row(spacing=4, controls=[ft.Icon(icono, size=12, color=color), ft.Text(arch, size=11, color=TEXT_DARK)])
                )
            if len(archivos) > 12:
                lista_archivos.append(ft.Text(f"… y {len(archivos)-12} archivo(s) más", size=11, color=TEXT_MUTED))
        else:
            lista_archivos.append(ft.Text("Sin archivos descargados", size=11, color=TEXT_MUTED, italic=True))

        return ft.Container(
            bgcolor=PAGE_BG,
            padding=ft.Padding.all(12),
            border_radius=ft.BorderRadius.all(8),
            border=ft.Border.all(1, BORDER_COLOR),
            content=ft.Column(
                spacing=8,
                controls=[
                    ft.Row(
                        alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                        controls=[
                            ft.Text(f"Folio: {folio}", size=14, color=TEXT_DARK, weight=ft.FontWeight.W_700),
                            ft.Row(
                                spacing=10,
                                controls=[
                                    ft.TextButton(
                                        "Abrir carpeta",
                                        icon=ft.Icons.FOLDER_OPEN,
                                        style=ft.ButtonStyle(color=TEAL_PRIMARY, padding=ft.Padding.symmetric(horizontal=8, vertical=0)),
                                        on_click=lambda e, p=carpeta: _abrir_ruta_al_frente(p),
                                    ),
                                    ft.Container(
                                        padding=ft.Padding.symmetric(horizontal=7, vertical=3),
                                        bgcolor=badge_color,
                                        border_radius=ft.BorderRadius.all(6),
                                        content=ft.Text(f"RPC Exactitud: {score}", size=11, color="#FFF", weight=ft.FontWeight.W_600),
                                    ),
                                ],
                            ),
                        ],
                    ),
                    ft.Column(
                        spacing=2,
                        controls=[
                            ft.Text(f"Nombre: {nombre}", size=12, color=TEXT_DARK, weight=ft.FontWeight.W_600),
                            ft.Text(f"Asunto: {asunto}", size=11, color=TEXT_GRAY),
                            ft.Row(
                                spacing=15,
                                wrap=True,
                                controls=[
                                    ft.Text(f"ID: {id_sol}", size=11, color=TEXT_GRAY),
                                    ft.Text(f"Rep. Legal: {rep_leg}", size=11, color=TEXT_GRAY),
                                    ft.Text(f"Fecha: {fecha_reg}", size=11, color=TEXT_GRAY),
                                ],
                            ),
                        ],
                    ),
                    ft.Divider(height=1, color=DIVIDER_COLOR),
                    ft.Column(spacing=2, controls=lista_archivos),
                ],
            ),
        )

    def _metadata_panel(self, resultados: list[dict]) -> ft.Control | None:
        cards = []
        for r in resultados:
            card = self._metadata_card(r)
            if card:
                cards.append(card)
        if not cards:
            return None
        return ft.Container(
            padding=ft.Padding.all(14),
            border_radius=ft.BorderRadius.all(10),
            border=ft.Border.all(1, BORDER_COLOR),
            bgcolor=CARD_BG,
            content=ft.Column(
                spacing=10,
                controls=[
                    ft.Row(
                        spacing=8,
                        vertical_alignment=ft.CrossAxisAlignment.CENTER,
                        controls=[
                            ft.Icon(ft.Icons.DATA_OBJECT, size=18, color=BLUE_INFO),
                            ft.Text("Metadatos extraídos por folio", size=13, color=TEXT_DARK, weight=ft.FontWeight.W_700),
                        ],
                    ),
                    ft.Column(spacing=8, controls=cards),
                ],
            ),
        )

    def _build_screen_resumen(self) -> ft.Control:
        data, source = self._load_summary_source()
        resultados = self._summary_results(data)

        if not data:
            msg = (
                "Ejecución actual en curso: todavía no se generan resultados para este TXT."
                if self._running
                else "No encontré resultados para el TXT seleccionado. Cuando ejecutes el proceso, aparecerán aquí."
            )
            return ft.Column(
                spacing=16,
                expand=True,
                controls=[
                    self._card(
                        "Resultados",
                        "Se actualiza automáticamente con la ejecución actual.",
                        ft.Column(
                            spacing=10,
                            controls=[
                                ft.Text(msg, size=13, color=TEXT_GRAY),
                                ft.Row(spacing=8, controls=[
                                    ft.ElevatedButton(
                                        "Actualizar",
                                        icon=ft.Icons.REFRESH,
                                        style=ft.ButtonStyle(bgcolor=TEAL_PRIMARY, color="white", shape=ft.RoundedRectangleBorder(radius=9)),
                                        on_click=lambda e: self._refresh_summary_if_visible(force=True),
                                    ),
                                    self._small_button("Abrir descargas/", ft.Icons.FOLDER, lambda e: _abrir_ruta_al_frente(self._descargas_dir_actual())),
                                ]),
                            ],
                        ),
                        icon=ft.Icons.SUMMARIZE_OUTLINED,
                    )
                ],
            )

        exitosos = [r for r in resultados if self._is_success_result(r)]
        duplicados = [r for r in resultados if self._is_duplicate_result(r)]
        baja = [r for r in resultados if self._is_low_match_result(r)]
        no_encontrados = [r for r in resultados if self._is_not_found_result(r)]

        # sin_operador = no hay rpc_ok ni rpc_resultado (no encontrado en catálogo)
        # pero SIN error adicional (solo faltan datos del catálogo, los archivos pueden estar bien).
        # Si además tienen errores reales (rpc_ok=false Y excel_ok=false Y organizado_ok=false),
        # también se muestran en Errores (igual que en el RESUMEN EJECUTIVO de la terminal).
        sin_operador_puro = [
            r for r in resultados
            if (not r.get("rpc_ok")) and not (r.get("rpc_resultado") or {})
            and not self._is_success_result(r) and r not in no_encontrados
            and r.get("excel_ok") is not False  # archivos descargados y organizados
        ]
        sin_operador_con_error = [
            r for r in resultados
            if (not r.get("rpc_ok")) and not (r.get("rpc_resultado") or {})
            and not self._is_success_result(r) and r not in no_encontrados
            and r.get("excel_ok") is False  # no se pudo organizar/procesar
        ]
        sin_operador = sin_operador_puro + sin_operador_con_error
        incompletos = [r for r in resultados if self._is_incomplete_result(r)
                       and r not in no_encontrados and r not in sin_operador]
        errores = [
            r for r in resultados
            if r not in exitosos and r not in duplicados and r not in baja
            and r not in no_encontrados and r not in incompletos and r not in sin_operador_puro
        ]

        # Antes aquí se mostraba una tarjeta azul de totales generales.
        # Se deja fuera de la interfaz para que Resultados muestre solo las secciones operativas.

        metadata_panel = self._metadata_panel(resultados)
        controls = [
            self._card(
                "Resultados",
                f"Fuente: {source}" if source else None,
                ft.Column(
                    spacing=10,
                    controls=[
                        ft.Row(spacing=8, controls=[
                            ft.ElevatedButton(
                                "Actualizar resultados",
                                icon=ft.Icons.REFRESH,
                                style=ft.ButtonStyle(bgcolor=TEAL_PRIMARY, color="white", shape=ft.RoundedRectangleBorder(radius=9)),
                                on_click=lambda e: self._rebuild(),
                            ),
                            self._small_button("Abrir descargas/", ft.Icons.FOLDER, lambda e: _abrir_ruta_al_frente(self._descargas_dir_actual())),
                            self._small_button("Abrir output/", ft.Icons.FOLDER_COPY, lambda e: _abrir_ruta_al_frente(OUTPUT_DIR)),
                        ]),
                        self._resumen_seccion(
                            "🟢 Éxito total",
                            exitosos,
                            GREEN_OK,
                            lambda r: (r.get("rpc_resultado") or {}).get("nombre_completo") or r.get("operador") or r.get("nombre") or "Organizado correctamente",
                        ),
                        self._resumen_seccion(
                            "🔴 Errores",
                            errores + incompletos + no_encontrados,
                            RED_ERR,
                            lambda r: (
                                r.get("error") or r.get("mensaje") or r.get("estado")
                                or ("Sin operador / no procesado" if not r.get("nombre_operador") else None)
                                or "Revisión necesaria"
                            ),
                        ),
                        self._resumen_seccion_sin_operador(sin_operador),
                    ],
                ),
                icon=ft.Icons.SUMMARIZE_OUTLINED,
            ),
        ]
        if metadata_panel:
            controls.append(metadata_panel)

        return ft.Column(spacing=16, expand=True, scroll=ft.ScrollMode.AUTO, controls=controls)

    # ════════════════════════════════════════════════════
    #  PANTALLA: SALIDAS
    # ════════════════════════════════════════════════════

    def _output_tile(self, title: str, path: Path, description: str, status_control: ft.Text, icon, is_folder=False) -> ft.Control:
        return ft.Container(
            bgcolor=SOFT_CARD_BG,
            border_radius=ft.BorderRadius.all(12),
            border=ft.Border.all(1, BORDER_COLOR),
            padding=ft.Padding.all(14),
            content=ft.Row(
                spacing=12,
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
                controls=[
                    ft.Container(
                        width=42,
                        height=42,
                        border_radius=ft.BorderRadius.all(10),
                        bgcolor="#E5F1F2",
                        content=ft.Icon(icon, color=TEAL_PRIMARY, size=22),
                    ),
                    ft.Column(
                        expand=True,
                        spacing=3,
                        controls=[
                            ft.Text(title, size=14, color=TEXT_DARK, weight=ft.FontWeight.W_700),
                            ft.Text(str(path), size=12, color=TEXT_GRAY),
                            ft.Text(description, size=12, color=TEXT_MUTED),
                            status_control,
                        ],
                    ),
                    self._small_button("Abrir", ft.Icons.OPEN_IN_NEW, lambda e, p=path, folder=is_folder: _abrir_ruta_al_frente(p, seleccionar_archivo=not folder)),
                ],
            ),
        )

    def _build_screen_salidas(self) -> ft.Control:
        self._refresh_outputs()
        outputs = ft.Column(
            spacing=12,
            controls=[
                self._output_tile(
                    "Excel de control",
                    EXCEL_CONTROL,
                    "Archivo principal actualizado por el flujo.",
                    self.out_excel_control_status,
                    ft.Icons.TABLE_CHART,
                ),
                self._output_tile(
                    "Excel consolidado de folios/registros",
                    EXCEL_CONSOLIDADO,
                    "Resumen completo generado dentro de output/.",
                    self.out_excel_consolidado_status,
                    ft.Icons.FACT_CHECK_OUTLINED,
                ),
                self._output_tile(
                    "Carpetas organizadas",
                    OUTPUT_DIR,
                    "Aquí quedan las carpetas finales por folio, registro u operador según el procesamiento.",
                    self.out_output_status,
                    ft.Icons.FOLDER_COPY,
                    is_folder=True,
                ),
                self._output_tile(
                    "Descargas temporales",
                    DESCARGAS_DIR,
                    "Carpetas de trabajo generadas durante la descarga desde SATyS.",
                    self.out_descargas_status,
                    ft.Icons.DOWNLOAD,
                    is_folder=True,
                ),
            ],
        )

        return ft.Column(
            spacing=16,
            expand=True,
            controls=[
                self._card(
                    "Salidas del proceso",
                    "Verifica y abre los archivos/carpeta que entrega la automatización.",
                    ft.Column(spacing=12, controls=[
                        ft.Row(spacing=8, controls=[
                            ft.ElevatedButton(
                                "Actualizar estado",
                                icon=ft.Icons.REFRESH,
                                style=ft.ButtonStyle(bgcolor=TEAL_PRIMARY, color="white", shape=ft.RoundedRectangleBorder(radius=9)),
                                on_click=lambda e: self._refresh_outputs_and_update(),
                            ),
                            self._small_button("Abrir output/", ft.Icons.FOLDER_COPY, lambda e: _abrir_ruta_al_frente(OUTPUT_DIR)),
                            self._small_button("Abrir TrámitesCRT.xlsx", ft.Icons.TABLE_CHART, lambda e: _abrir_ruta_al_frente(EXCEL_CONTROL, seleccionar_archivo=True)),
                        ]),
                        outputs,
                    ]),
                    icon=ft.Icons.OUTPUT,
                )
            ],
        )

    # ════════════════════════════════════════════════════
    #  PANTALLA: HISTORIAL
    # ════════════════════════════════════════════════════

    def _history_row(self, item: dict) -> ft.Control:
        ok = item.get("return_code") == 0
        return ft.Container(
            bgcolor=SOFT_CARD_BG,
            border_radius=ft.BorderRadius.all(10),
            border=ft.Border.all(1, BORDER_COLOR),
            padding=ft.Padding.all(12),
            content=ft.Row(
                controls=[
                    ft.Icon(ft.Icons.CHECK_CIRCLE if ok else ft.Icons.ERROR, color=GREEN_OK if ok else RED_ERR, size=20),
                    ft.Column(
                        expand=True,
                        spacing=2,
                        controls=[
                            ft.Text(item.get("when", "—"), size=13, color=TEXT_DARK, weight=ft.FontWeight.W_700),
                            ft.Text(item.get("kind", "—"), size=12, color=TEXT_GRAY),
                            ft.Text(item.get("txt", "—"), size=11, color=TEXT_MUTED),
                        ],
                    ),
                    ft.Text(f"Código {item.get('return_code', '—')}", size=12, color=GREEN_OK if ok else RED_ERR),
                ],
            ),
        )

    def _build_screen_historial(self) -> ft.Control:
        hist = self._cfg.get("historial", []) or []
        self._historial = hist
        controls = [self._history_row(x) for x in reversed(hist[-20:])]
        if not controls:
            controls = [ft.Text("Todavía no hay ejecuciones registradas en esta interfaz.", size=13, color=TEXT_MUTED)]
        card = self._card(
            "Historial de ejecuciones",
            "Últimas ejecuciones lanzadas desde esta UI.",
            ft.Column(spacing=10, controls=controls),
            icon=ft.Icons.HISTORY,
        )
        return ft.Column(expand=True, scroll=ft.ScrollMode.AUTO, controls=[card])

    # ════════════════════════════════════════════════════
    #  PANTALLA: CONFIGURACIÓN
    # ════════════════════════════════════════════════════

    def _build_screen_config(self) -> ft.Control:
        return ft.Column(
            spacing=16,
            expand=True,
            controls=[
                self._card(
                    "Configuración del entorno",
                    "Solo cambia esto si moviste el Python portable o el script principal.",
                    ft.Column(
                        spacing=12,
                        controls=[
                            ft.Row(spacing=10, controls=[
                                self.txt_python,
                                self._small_button("Examinar", ft.Icons.SEARCH, lambda e: self._choose_python()),
                            ]),
                            ft.Row(spacing=10, controls=[
                                self.txt_script,
                                self._small_button("Examinar", ft.Icons.SEARCH, lambda e: self._choose_script()),
                            ]),
                            ft.Row(spacing=10, controls=[
                                ft.ElevatedButton(
                                    "Guardar configuración",
                                    icon=ft.Icons.SAVE,
                                    style=ft.ButtonStyle(bgcolor=TEAL_PRIMARY, color="white", shape=ft.RoundedRectangleBorder(radius=9)),
                                    on_click=lambda e: self._save_ui_config(show=True),
                                ),
                                self._small_button("Restaurar recomendados", ft.Icons.RESTORE, lambda e: self._reset_defaults()),
                            ]),
                        ],
                    ),
                    icon=ft.Icons.SETTINGS,
                ),
                self._card(
                    "Recomendación de uso",
                    None,
                    ft.Column(spacing=8, controls=[
                        ft.Text("• Entrada: un archivo TXT con un registro o folio por línea.", size=13, color=TEXT_GRAY),
                        ft.Text(f"• Credenciales: {CREDENCIALES_FILE} usa dos líneas: usuario y contraseña.", size=13, color=TEXT_GRAY),
                        ft.Text("• Para producción: 6 ventanas y modo rápido sin navegador visible.", size=13, color=TEXT_GRAY),
                        ft.Text("• Las salidas importantes son TrámitesCRT.xlsx y la carpeta output/.", size=13, color=TEXT_GRAY),
                        ft.Text("• Los botones que abren ventanas usan Win32 para aparecer delante del navegador.", size=13, color=TEXT_GRAY),
                    ]),
                    icon=ft.Icons.INFO_OUTLINE,
                ),
            ],
        )

    # ════════════════════════════════════════════════════
    #  EVENTOS DE NAVEGACIÓN / FORMULARIO
    # ════════════════════════════════════════════════════

    def _toggle_sidebar(self, e=None) -> None:
        self._sidebar_visible = not self._sidebar_visible
        self._rebuild()

    def _select_nav(self, label: str) -> None:
        self.active_nav = label
        titles = {
            "Procesar": "Procesamiento SATyS",
            "Resumen": "Resultados",
            "Salidas": "Archivos generados",
            "Historial": "Historial",
            "Configuración": "Configuración",
        }
        self.header_title.value = titles.get(label, label)
        # No asignamos aquí screen_container.content. _rebuild() llama a build(),
        # y build() usa _build_active_screen() para pintar la pantalla correcta.
        self._rebuild()

    def _on_form_change(self, e=None) -> None:
        self._items_detectados = _line_count_txt(self.txt_archivo.value or "")
        self.counter_label.value = f"{self._items_detectados} elementos detectados"
        self._update_command_preview()
        self._save_ui_config(show=False)
        try:
            changed_control = getattr(e, "control", None)
            if changed_control in (self.dd_tipo_txt, self.txt_archivo):
                self._last_summary_refresh_ts = 0.0
                self._refresh_summary_if_visible(force=True)
            else:
                self.page.update()
        except Exception:
            pass

    def _handle_keyboard(self, e: ft.KeyboardEvent) -> None:
        key = (e.key or "").upper()
        if key == "F5" and not self._running:
            self._start_process(None)
        elif key == "ESCAPE" and self._running:
            self._stop_process(None)

    # ════════════════════════════════════════════════════
    #  EXAMINAR / ABRIR ARCHIVOS
    # ════════════════════════════════════════════════════

    def _choose_txt(self, e=None) -> None:
        current = Path(self.txt_archivo.value).parent if self.txt_archivo.value else Path.cwd()
        ruta = _abrir_dialogo_archivo_al_frente(
            "Selecciona archivo TXT de registros o folios",
            "Archivos TXT (*.txt)|*.txt|Todos los archivos (*.*)|*.*",
            str(current if current.exists() else Path.cwd()),
        )
        if ruta:
            self.txt_archivo.value = ruta
            self._on_form_change()
            self._last_summary_refresh_ts = 0.0
            self._refresh_summary_if_visible(force=True)
            self._show_toast("Archivo TXT seleccionado")

    def _choose_python(self) -> None:
        ruta = _abrir_dialogo_archivo_al_frente(
            "Selecciona python.exe",
            "Python executable (python.exe)|python.exe|Ejecutables (*.exe)|*.exe|Todos los archivos (*.*)|*.*",
            str(Path.cwd()),
        )
        if ruta:
            self.txt_python.value = ruta
            self._on_form_change()

    def _choose_script(self) -> None:
        ruta = _abrir_dialogo_archivo_al_frente(
            "Selecciona main_procesar.py",
            "Python (*.py)|*.py|Todos los archivos (*.*)|*.*",
            str(Path.cwd()),
        )
        if ruta:
            self.txt_script.value = ruta
            self._on_form_change()

    # ════════════════════════════════════════════════════
    #  COMANDO / VALIDACIÓN / CONFIG
    # ════════════════════════════════════════════════════

    def _current_credentials(self) -> tuple[str, str]:
        usuario = (self.txt_satys_user.value or "").strip()
        password = (self.txt_satys_pass.value or "").strip()
        return usuario, password

    def _reload_credentials(self, e=None) -> None:
        usuario, password, source = _read_satys_credentials()
        self.txt_satys_user.value = usuario
        self.txt_satys_pass.value = password
        self.cred_status.value = source
        self.cred_status.color = GREEN_OK if usuario and password else ORANGE_WARN
        self._show_toast("Credenciales recargadas")
        try:
            self.page.update()
        except Exception:
            pass

    def _save_credentials_to_file(self, e=None) -> None:
        usuario, password = self._current_credentials()
        if not usuario or not password:
            self.cred_status.value = "No guardé: usuario y contraseña SATyS son obligatorios."
            self.cred_status.color = RED_ERR
            self._show_toast("Faltan credenciales", seconds=3)
            try:
                self.page.update()
            except Exception:
                pass
            return
        ok, msg = _write_satys_credentials(usuario, password)
        self.cred_status.value = msg
        self.cred_status.color = GREEN_OK if ok else RED_ERR
        self._show_toast("Credenciales guardadas" if ok else "No se pudo guardar", seconds=3)
        try:
            self.page.update()
        except Exception:
            pass

    def _save_ui_config(self, show: bool = False) -> None:
        self._cfg.update({
            "input_kind": self.dd_tipo_txt.value,
            "txt_path": self.txt_archivo.value or "",
            "workers": self.txt_workers.value or str(DEFAULT_WORKERS),
            "headless": bool(self.sw_headless.value),
            "python_path": self.txt_python.value or _python_exe_default(),
            "script_path": self.txt_script.value or DEFAULT_SCRIPT,
        })
        _save_config(self._cfg)
        if show:
            self._show_toast("Configuración guardada")

    def _reset_defaults(self) -> None:
        self.txt_python.value = _python_exe_default()
        self.txt_script.value = DEFAULT_SCRIPT
        self.txt_workers.value = str(DEFAULT_WORKERS)
        self.sw_headless.value = DEFAULT_HEADLESS
        self.dd_tipo_txt.value = "registros"
        self._on_form_change()
        self._show_toast("Valores recomendados restaurados")

    def _build_args(self) -> list[str]:
        python_path = (self.txt_python.value or _python_exe_default()).strip()
        script_path = (self.txt_script.value or DEFAULT_SCRIPT).strip()
        txt_path = (self.txt_archivo.value or "").strip().strip('"')

        args = [python_path, script_path]
        if self.dd_tipo_txt.value == "folios":
            args += ["--archivo-folios", txt_path]
        else:
            args += ["--archivo-registro", txt_path]

        workers = (self.txt_workers.value or str(DEFAULT_WORKERS)).strip()
        if workers:
            args += ["--workers", workers]

        if self.sw_headless.value:
            args.append("--headless")

        return args

    def _update_command_preview(self) -> None:
        try:
            self.command_preview.value = _command_to_string(self._build_args())
        except Exception as ex:
            self.command_preview.value = f"No se pudo preparar el comando: {ex}"

    def _validate_before_run(self) -> tuple[bool, str]:
        python_path = Path((self.txt_python.value or "").strip().strip('"'))
        script_path = Path((self.txt_script.value or "").strip().strip('"'))
        txt_path = Path((self.txt_archivo.value or "").strip().strip('"'))

        if not self.txt_archivo.value.strip():
            return False, "Selecciona un archivo TXT de entrada."
        if not txt_path.exists() or not txt_path.is_file():
            return False, f"El TXT no existe:\n{txt_path}"
        if txt_path.suffix.lower() != ".txt":
            return False, "La entrada debe ser un archivo .txt."
        if _line_count_txt(str(txt_path)) <= 0:
            return False, "El TXT no tiene registros o folios válidos."
        if not script_path.exists() or not script_path.is_file():
            return False, f"No encontré el script principal:\n{script_path}"
        if python_path.name.lower().endswith("python.exe") and not python_path.exists():
            return False, f"No encontré el Python indicado:\n{python_path}"
        try:
            workers = int((self.txt_workers.value or "0").strip())
            if workers < 1 or workers > 20:
                return False, "Ventanas debe estar entre 1 y 20."
        except Exception:
            return False, "Ventanas debe ser un número."
        satys_user, satys_pass = self._current_credentials()
        if not satys_user or not satys_pass:
            return False, (
                "Faltan credenciales SATyS. Escríbelas en Usuario/Contraseña "
                "o guarda el archivo de credenciales."
            )
        return True, "OK"

    # ════════════════════════════════════════════════════
    #  EJECUCIÓN
    # ════════════════════════════════════════════════════

    def _start_process(self, e=None) -> None:
        if self._running:
            return

        self._on_form_change()
        ok, msg = self._validate_before_run()
        if not ok:
            self._show_toast(msg, seconds=3)
            self._append_log(f"[{_ts()}] ❌ {msg}", LOG_ERROR)
            return

        args = self._build_args()
        self._save_ui_config(show=False)
        self._prepare_current_run_summary()
        self._set_running(True)
        self._clear_log(None)
        self._refresh_summary_if_visible(force=True)
        self._start_summary_auto_refresh()
        self._append_log("═" * 72, "#A0BBCC")
        self._append_log(f"[{_ts()}] 🚀 Iniciando automatización SATyS", LOG_INFO)
        self._append_log(f"[{_ts()}] Entrada: {self.dd_tipo_txt.options[0].text if self.dd_tipo_txt.value == 'registros' else self.dd_tipo_txt.options[1].text}", LOG_INFO)
        self._append_log(f"[{_ts()}] TXT: {self.txt_archivo.value}", LOG_INFO)
        satys_user, satys_pass = self._current_credentials()
        self._append_log(f"[{_ts()}] Elementos detectados: {_line_count_txt(self.txt_archivo.value)}", LOG_INFO)
        self._append_log(f"[{_ts()}] Usuario SATyS: {satys_user}", LOG_INFO)
        self._append_log(f"[{_ts()}] Archivo credenciales: {CREDENCIALES_FILE}", LOG_MUTED)
        self._append_log(f"[{_ts()}] Comando: {_command_to_string(args)}", LOG_MUTED)
        self._append_log("═" * 72, "#A0BBCC")

        def run_process() -> None:
            try:
                env = os.environ.copy()
                env["PYTHONIOENCODING"] = "utf-8"
                env["PYTHONUNBUFFERED"] = "1"
                env["SATYS_USER"] = satys_user
                env["SATYS_PASS"] = satys_pass
                env["SATYS_CREDENTIALS_FILE"] = str(CREDENCIALES_FILE)
                script_path = Path((self.txt_script.value or DEFAULT_SCRIPT).strip().strip('"'))
                cwd = str(script_path.resolve().parent) if script_path.exists() else None

                self._process = subprocess.Popen(
                    args,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    env=env,
                    cwd=cwd,
                    bufsize=1,
                )
                assert self._process.stdout is not None
                for line in self._process.stdout:
                    self._log_queue.put(line.rstrip("\n"))
                self._process.wait()
                self._log_queue.put(("__done__", self._process.returncode))
            except Exception as ex:
                self._log_queue.put(f"❌ Error al lanzar proceso: {ex}")
                self._log_queue.put(("__done__", -1))

        def drain_log() -> None:
            rc = None
            while True:
                batch = []
                try:
                    while len(batch) < 30:
                        item = self._log_queue.get(timeout=0.05)
                        if isinstance(item, tuple) and item[0] == "__done__":
                            rc = item[1]
                            break
                        batch.append(item)
                except queue.Empty:
                    pass

                if batch:
                    for line in batch:
                        self._append_log(line, _color_for_line(line))
                    self._refresh_summary_if_visible(force=False)
                    try:
                        self.page.update()
                    except Exception:
                        pass

                if rc is not None:
                    break

            self._finish_process(int(rc or 0))

        threading.Thread(target=run_process, daemon=True).start()
        threading.Thread(target=drain_log, daemon=True).start()
        try:
            self.page.update()
        except Exception:
            pass

    def _stop_process(self, e=None) -> None:
        if not self._running or not self._process:
            return
        self._append_log(f"[{_ts()}] ⚠️ Deteniendo proceso por solicitud del usuario...", LOG_WARNING)
        try:
            self._process.terminate()
            try:
                self._process.wait(timeout=4)
            except subprocess.TimeoutExpired:
                self._process.kill()
        except Exception as ex:
            self._append_log(f"[{_ts()}] ❌ No se pudo detener: {ex}", LOG_ERROR)
        self._set_running(False)

    def _finish_process(self, rc: int) -> None:
        if rc == 0:
            self._append_log(f"[{_ts()}] ✅ Proceso completado correctamente.", LOG_SUCCESS)
        else:
            self._append_log(f"[{_ts()}] ❌ Proceso terminó con código {rc}.", LOG_ERROR)
        self._set_running(False)
        self._refresh_outputs()
        self._add_history(rc)
        self._refresh_summary_if_visible(force=True)
        try:
            self.page.update()
        except Exception:
            pass

    def _set_running(self, running: bool) -> None:
        self._running = running
        self.btn_iniciar.visible = not running
        self.btn_detener.visible = running
        self.progress.visible = running
        self.progress.value = None if running else 0
        self.status_icon.color = TEAL_PROGRESS if running else TEXT_MUTED
        self.status_label.value = "Procesando..." if running else "Listo"
        self.status_label.color = TEAL_PRIMARY if running else TEXT_MUTED

    def _add_history(self, rc: int) -> None:
        kind = "Números de registro CRT" if self.dd_tipo_txt.value == "registros" else "Folios SATyS"
        hist = self._cfg.get("historial", []) or []
        hist.append({
            "when": _dt(),
            "return_code": rc,
            "kind": kind,
            "txt": self.txt_archivo.value,
            "count": _line_count_txt(self.txt_archivo.value),
        })
        self._cfg["historial"] = hist[-50:]
        _save_config(self._cfg)

    # ════════════════════════════════════════════════════
    #  LOG
    # ════════════════════════════════════════════════════

    def _append_log(self, text: str, color: str = LOG_TEXT) -> None:
        self._log_lines.append(text)
        try:
            print(text, flush=True)
        except Exception:
            pass
        self.log_view.controls.append(
            ft.Text(
                text,
                size=12,
                color=color,
                font_family="Consolas",
                selectable=True,
                no_wrap=True,
            )
        )

    def _clear_log(self, e=None) -> None:
        self._log_lines.clear()
        self.log_view.controls.clear()
        try:
            self.page.update()
        except Exception:
            pass

    def _copy_log(self, e=None) -> None:
        text = "\n".join(self._log_lines)
        try:
            self.page.set_clipboard(text)
            self._show_toast("Log copiado")
        except Exception:
            self._show_toast("No se pudo copiar el log")

    # ════════════════════════════════════════════════════
    #  SALIDAS
    # ════════════════════════════════════════════════════

    def _status_text_for_path(self, path: Path, is_folder: bool = False) -> tuple[str, str]:
        if path.exists():
            if is_folder:
                try:
                    count = len([x for x in path.iterdir()])
                except Exception:
                    count = 0
                return f"✅ Existe · {count} elemento(s)", GREEN_OK
            try:
                size_kb = path.stat().st_size / 1024
                mod = datetime.fromtimestamp(path.stat().st_mtime).strftime("%Y-%m-%d %H:%M")
                return f"✅ Existe · {size_kb:,.1f} KB · modificado {mod}", GREEN_OK
            except Exception:
                return "✅ Existe", GREEN_OK
        return "⚪ Aún no existe", TEXT_MUTED

    def _refresh_outputs(self) -> None:
        for control, path, is_folder in [
            (self.out_excel_control_status, EXCEL_CONTROL, False),
            (self.out_excel_consolidado_status, EXCEL_CONSOLIDADO, False),
            (self.out_output_status, OUTPUT_DIR, True),
            (self.out_descargas_status, DESCARGAS_DIR, True),
        ]:
            text, color = self._status_text_for_path(path, is_folder=is_folder)
            control.value = text
            control.color = color

    def _refresh_outputs_and_update(self) -> None:
        self._refresh_outputs()
        self._show_toast("Estado actualizado")
        try:
            self.page.update()
        except Exception:
            pass


# ════════════════════════════════════════════════════════
#  ENTRY POINT
# ════════════════════════════════════════════════════════


def main(page: ft.Page):
    page.title = "SATyS — Gestor CRT"
    page.bgcolor = PAGE_BG
    page.padding = 0

    # Ventana web local grande, pensada para uso operativo.
    try:
        page.window.width = 1440
        page.window.height = 880
        page.window.min_width = 1080
        page.window.min_height = 680
        page.window.resizable = True
        page.window.maximizable = True
    except Exception:
        pass

    page.theme = ft.Theme(font_family="Segoe UI", color_scheme_seed=TEAL_PRIMARY)

    # Consola Windows UTF-8 para logs.
    try:
        if hasattr(sys.stdout, "buffer") and getattr(sys.stdout, "encoding", "") != "utf-8":
            sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
        if hasattr(sys.stderr, "buffer") and getattr(sys.stderr, "encoding", "") != "utf-8":
            sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")
    except Exception:
        pass

    app = SATySApp(page)
    page.add(app.build())


if __name__ == "__main__":
    ft.run(main, assets_dir="assets", view=ft.AppView.WEB_BROWSER)
