#!/usr/bin/env python3
"""
=============================================================
  UI — GESTOR DE AUTOMATIZACIÓN SATyS / CRT  (v2)
=============================================================
Mejoras respecto a la versión anterior:
  ✅ Botón "Examinar" para seleccionar archivo TXT (FilePicker nativo)
  ✅ Botón "Examinar" para ruta de Python y script
  ✅ Botón de acceso rápido a carpeta de descargas / output
  ✅ Ejecución más rápida: unbuffered + drain más agresivo
  ✅ Contador de folios en tiempo real en el encabezado
  ✅ Atajos de teclado: F5 = Iniciar, Esc = Detener
  ✅ Botón "Copiar log" para depuración rápida
  ✅ Panel lateral colapsable para ganar espacio en pantallas pequeñas
  ✅ Tamaño de ventana inicial más grande y redimensionable
  ✅ Persistencia de config en config_satys.json

Uso:
  python ui_satys_mejorado.py
=============================================================
"""

import io
import os
import sys
import json
import time
import queue
import logging
import threading
import subprocess
from pathlib import Path
from datetime import datetime

import flet as ft

# ════════════════════════════════════════════════════════
#  PALETA DE COLORES (basada en el logo CRT — sin cambios)
# ════════════════════════════════════════════════════════

PAGE_BG            = "#F1F3F5"
SIDEBAR_BG         = "#D9E7E7"
SIDEBAR_SEL_BG     = "#C3D6D7"
CARD_BG            = "#FFFFFF"
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

DEFAULT_SCRIPT     = "main_procesar.py"
DEFAULT_PYTHON     = r"python_portable\python.exe"
DEFAULT_FOLIO_INI  = "6407"
DEFAULT_FOLIO_FIN  = "6433"
DESCARGA_BASE      = Path("descargas")
CONFIG_FILE        = Path("config_satys.json")

NAV_ITEMS = [
    ("Procesar Folios",  ft.Icons.PLAY_CIRCLE_OUTLINE, ft.Icons.PLAY_CIRCLE),
    ("Historial",        ft.Icons.HISTORY,              ft.Icons.HISTORY),
    ("Configuración",    ft.Icons.SETTINGS_OUTLINED,    ft.Icons.SETTINGS),
]

# ════════════════════════════════════════════════════════
#  HELPERS
# ════════════════════════════════════════════════════════

def _color_for_line(line: str) -> str:
    l = line.lower()
    if any(t in l for t in ["✅", "éxito", "exitoso", "ok", "completad", "guardad", "listo", "copiad"]):
        return LOG_SUCCESS
    if any(t in l for t in ["❌", "error", "crítico", "falló", "fail", "critical"]):
        return LOG_ERROR
    if any(t in l for t in ["⚠️", "warn", "advertencia", "revisión", "empate", "baja"]):
        return LOG_WARNING
    if any(t in l for t in ["📥", "📄", "📊", "📁", "🔍", "🆔", "🌐", "💾", "📅", "➕", "📋", "🗂️"]):
        return LOG_INFO
    if any(t in l for t in ["───", "═══", "parte", "folio", "procesando", "iniciando"]):
        return "#A0BBCC"
    return LOG_TEXT


def _ts() -> str:
    return datetime.now().strftime("%H:%M:%S")


def _python_exe() -> str:
    portable = Path(DEFAULT_PYTHON)
    if portable.exists():
        return str(portable)
    return sys.executable


def _load_config() -> dict:
    """Carga configuración persistida."""
    if CONFIG_FILE.exists():
        try:
            return json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {}


def _save_config(data: dict):
    """Guarda configuración en disco."""
    try:
        CONFIG_FILE.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    except Exception:
        pass


def _abrir_carpeta(ruta: str):
    """Abre carpeta (o archivo) en el explorador de Windows forzando el primer plano."""
    import subprocess
    import ctypes
    from pathlib import Path
    import os
    
    p = Path(ruta)
    if p.suffix:
        p.parent.mkdir(parents=True, exist_ok=True)
    else:
        p.mkdir(parents=True, exist_ok=True)
        
    try:
        # 1. HACK LEGENDARIO DE WINDOWS: Presionar y soltar ALT (VK_MENU = 0x12)
        # Esto engaña al sistema para que retire el bloqueo de foco (ForegroundLockTimeout)
        ctypes.windll.user32.keybd_event(0x12, 0, 0, 0)
        ctypes.windll.user32.keybd_event(0x12, 0, 2, 0)
        
        # 2. Permiso explícito de la API
        ctypes.windll.user32.AllowSetForegroundWindow(-1)
        
        # 3. Lanzar con 'explorer /n' (para forzar una ventana nueva y no reusar una oculta)
        path_str = str(p.resolve())
        if p.is_file():
            subprocess.Popen(["explorer", "/select,", path_str])
        else:
            subprocess.Popen(["explorer", "/n,", path_str])
            
    except Exception:
        try:
            os.startfile(str(p.resolve()))
        except Exception:
            pass


def _abrir_dialogo_archivo(titulo: str, filtro: str) -> str:
    """Abre un diálogo nativo de selección de archivo usando la API de Windows (ctypes)."""
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
            ("FlagsEx", wintypes.DWORD)
        ]

    # Convertir filtro estilo "Archivos|*.txt" a formato null-terminated de Windows
    filter_nulls = filtro.replace('|', '\0') + '\0\0'
    file_buffer = ctypes.create_unicode_buffer(260)
    
    ofn = OPENFILENAMEW()
    ofn.lStructSize = ctypes.sizeof(OPENFILENAMEW)
    
    # Asignamos como dueña a la ventana activa actual (el navegador web) para forzar que salga por encima
    ofn.hwndOwner = ctypes.windll.user32.GetForegroundWindow()
    
    ofn.lpstrFilter = filter_nulls
    ofn.lpstrFile = ctypes.cast(file_buffer, wintypes.LPWSTR)
    ofn.nMaxFile = 260
    ofn.lpstrTitle = titulo
    ofn.Flags = 0x00080000 | 0x00001000 | 0x00000008 # OFN_EXPLORER | OFN_FILEMUSTEXIST | OFN_NOCHANGEDIR
    
    if GetOpenFileNameW(ctypes.byref(ofn)):
        return file_buffer.value
    return ""


# ════════════════════════════════════════════════════════
#  CLASE PRINCIPAL
# ════════════════════════════════════════════════════════

class SATySApp:
    def __init__(self, page: ft.Page):
        self.page = page
        self.active_nav = "Procesar Folios"

        # Estado del proceso
        self._process: subprocess.Popen | None = None
        self._running = False
        self._log_queue: queue.Queue = queue.Queue()
        self._resultados: list = []
        self._folios_procesados = 0
        self._sidebar_visible = True

        # Cargar config persistida
        cfg = _load_config()

        # ── FilePickers (Removidos para compatibilidad web) ────
        self._ruta_txt_interna = ""

        # ── Campos de texto ────────────────────────────────
        self.txt_folio_ini = ft.TextField(
            value=cfg.get("folio_ini", DEFAULT_FOLIO_INI), width=120,
            text_size=14, height=40,
            border_color=BORDER_COLOR, focused_border_color=TEAL_PRIMARY,
            border_radius=ft.BorderRadius.all(8),
            content_padding=ft.Padding.symmetric(vertical=6, horizontal=10),
        )
        self.txt_folio_fin = ft.TextField(
            value=cfg.get("folio_fin", DEFAULT_FOLIO_FIN), width=120,
            text_size=14, height=40,
            border_color=BORDER_COLOR, focused_border_color=TEAL_PRIMARY,
            border_radius=ft.BorderRadius.all(8),
            content_padding=ft.Padding.symmetric(vertical=6, horizontal=10),
        )
        self.txt_folios_manual = ft.TextField(
            hint_text="Ej: 6407, 6801, 6802",
            hint_style=ft.TextStyle(color=TEXT_MUTED, size=13),
            text_size=13, height=40, expand=True,
            border_color=BORDER_COLOR, focused_border_color=TEAL_PRIMARY,
            border_radius=ft.BorderRadius.all(8),
            content_padding=ft.Padding.symmetric(vertical=6, horizontal=10),
        )
        self.txt_archivo_folios = ft.TextField(
            hint_text="Pega la ruta o clic en 'Examinar'",
            text_size=13, height=40, expand=True,
            border_color=BORDER_COLOR, focused_border_color=TEAL_PRIMARY,
            border_radius=ft.BorderRadius.all(8),
            content_padding=ft.Padding.symmetric(vertical=6, horizontal=10),
            read_only=False,  # Ahora es editable por si prefieren copiar/pegar
        )
        self.txt_workers = ft.TextField(
            value=str(cfg.get("workers", 10)), width=90,
            text_size=13, height=40,
            label="Workers",
            border_color=BORDER_COLOR, focused_border_color=TEAL_PRIMARY,
            border_radius=ft.BorderRadius.all(8),
            content_padding=ft.Padding.symmetric(vertical=6, horizontal=10),
        )
        self.txt_python_path = ft.TextField(
            value=cfg.get("python_path", _python_exe()),
            text_size=12, height=40, expand=True,
            border_color=BORDER_COLOR, focused_border_color=TEAL_PRIMARY,
            border_radius=ft.BorderRadius.all(8),
            content_padding=ft.Padding.symmetric(vertical=6, horizontal=10),
        )
        self.txt_script_path = ft.TextField(
            value=cfg.get("script_path", DEFAULT_SCRIPT),
            text_size=12, height=40, expand=True,
            border_color=BORDER_COLOR, focused_border_color=TEAL_PRIMARY,
            border_radius=ft.BorderRadius.all(8),
            content_padding=ft.Padding.symmetric(vertical=6, horizontal=10),
        )

        # ── Dropdown de modo ───────────────────────────────
        self.dd_modo = ft.Dropdown(
            value=cfg.get("modo", "rango"),
            options=[
                ft.dropdown.Option("rango",  "Rango de folios"),
                ft.dropdown.Option("manual", "Folios específicos"),
                ft.dropdown.Option("archivo","Archivo TXT de folios"),
                ft.dropdown.Option("todos",  "Todos en carpeta descargas/"),
            ],
            width=210, text_size=13, height=42,
            border_color=BORDER_COLOR, focused_border_color=TEAL_PRIMARY,
            border_radius=ft.BorderRadius.all(8),
            content_padding=ft.Padding.symmetric(vertical=4, horizontal=10),
            on_select=self._on_modo_change,
        )

        # ── Switch navegador ───────────────────────────────
        self.sw_navegador = ft.Switch(
            label="Ver navegador",
            value=cfg.get("ver_navegador", True),
            active_color=TEAL_PRIMARY,
        )

        # ── Filas de folios ────────────────────────────────
        self.folio_rango_row = ft.Row(
            spacing=10, vertical_alignment=ft.CrossAxisAlignment.CENTER,
            controls=[
                ft.Text("Desde:", size=13, color=TEXT_GRAY),
                self.txt_folio_ini,
                ft.Text("Hasta:", size=13, color=TEXT_GRAY),
                self.txt_folio_fin,
            ],
        )
        self.folio_manual_row = ft.Row(
            spacing=10, vertical_alignment=ft.CrossAxisAlignment.CENTER,
            visible=False,
            controls=[
                ft.Text("Folios:", size=13, color=TEXT_GRAY),
                self.txt_folios_manual,
            ],
        )

        # ── Fila de archivo con botón Examinar ────────────
        self.btn_examinar_txt = ft.Button(
            content=ft.Row(
                spacing=6, tight=True,
                controls=[
                    ft.Icon(ft.Icons.FOLDER_OPEN, size=15, color="#FFF"),
                    ft.Text("Examinar", size=12, color="#FFF"),
                ],
            ),
            bgcolor=TEAL_PRIMARY,
            style=ft.ButtonStyle(
                shape=ft.RoundedRectangleBorder(radius=8),
                padding=ft.Padding.symmetric(horizontal=14, vertical=10),
                elevation=0,
            ),
            on_click=self._on_pick_txt,
        )
        self.folio_archivo_row = ft.Row(
            spacing=8, vertical_alignment=ft.CrossAxisAlignment.CENTER,
            visible=False,
            controls=[
                ft.Text("Archivo:", size=13, color=TEXT_GRAY),
                self.txt_archivo_folios,
                self.btn_examinar_txt,
            ],
        )
        self.folio_todos_text = ft.Text(
            "Se procesarán todas las carpetas dentro de descargas/",
            size=13, color=TEXT_MUTED, italic=True, visible=False,
        )

        # ── Botones de acción ──────────────────────────────
        self.btn_iniciar = ft.Button(
            content=ft.Row(
                spacing=8, tight=True,
                controls=[
                    ft.Icon(ft.Icons.PLAY_ARROW, size=18, color="#FFF"),
                    ft.Text("Iniciar  (F5)", size=14, color="#FFF", weight=ft.FontWeight.W_600),
                ],
            ),
            bgcolor=TEAL_PRIMARY,
            style=ft.ButtonStyle(
                shape=ft.RoundedRectangleBorder(radius=10),
                padding=ft.Padding.symmetric(horizontal=22, vertical=14),
                elevation=0,
            ),
            on_click=self._on_iniciar,
            tooltip="Iniciar procesamiento (F5)",
        )
        self.btn_detener = ft.Button(
            content=ft.Row(
                spacing=8, tight=True,
                controls=[
                    ft.Icon(ft.Icons.STOP, size=18, color="#FFF"),
                    ft.Text("Detener (Esc)", size=14, color="#FFF", weight=ft.FontWeight.W_600),
                ],
            ),
            bgcolor=RED_ERR,
            style=ft.ButtonStyle(
                shape=ft.RoundedRectangleBorder(radius=10),
                padding=ft.Padding.symmetric(horizontal=22, vertical=14),
                elevation=0,
            ),
            visible=False,
            on_click=self._on_detener,
            tooltip="Detener proceso (Esc)",
        )
        self.btn_limpiar = ft.TextButton(
            content=ft.Row(
                spacing=6, tight=True,
                controls=[
                    ft.Icon(ft.Icons.DELETE_OUTLINE, size=16, color=TEXT_GRAY),
                    ft.Text("Limpiar log", size=13, color=TEXT_GRAY),
                ],
            ),
            on_click=self._on_limpiar_log,
        )
        self.btn_copiar_log = ft.TextButton(
            content=ft.Row(
                spacing=6, tight=True,
                controls=[
                    ft.Icon(ft.Icons.COPY, size=16, color=TEXT_GRAY),
                    ft.Text("Copiar log", size=13, color=TEXT_GRAY),
                ],
            ),
            on_click=self._on_copiar_log,
            tooltip="Copia todo el log al portapapeles",
        )

        # ── Botones de acceso rápido a carpetas ───────────
        self.btn_abrir_descargas = ft.OutlinedButton(
            content=ft.Row(
                spacing=6, tight=True,
                controls=[
                    ft.Icon(ft.Icons.FOLDER, size=15, color=TEAL_PRIMARY),
                    ft.Text("descargas/", size=12, color=TEAL_PRIMARY),
                ],
            ),
            style=ft.ButtonStyle(
                shape=ft.RoundedRectangleBorder(radius=8),
                side=ft.BorderSide(1, TEAL_PRIMARY),
                padding=ft.Padding.symmetric(horizontal=12, vertical=8),
            ),
            on_click=lambda e: _abrir_carpeta("descargas"),
            tooltip="Abrir carpeta de descargas",
        )
        self.btn_abrir_output = ft.OutlinedButton(
            content=ft.Row(
                spacing=6, tight=True,
                controls=[
                    ft.Icon(ft.Icons.DRIVE_FILE_MOVE, size=15, color=TEAL_PRIMARY),
                    ft.Text("output/", size=12, color=TEAL_PRIMARY),
                ],
            ),
            style=ft.ButtonStyle(
                shape=ft.RoundedRectangleBorder(radius=8),
                side=ft.BorderSide(1, TEAL_PRIMARY),
                padding=ft.Padding.symmetric(horizontal=12, vertical=8),
            ),
            on_click=lambda e: _abrir_carpeta("output"),
            tooltip="Abrir carpeta de salida",
        )
        self.btn_abrir_excel = ft.OutlinedButton(
            content=ft.Row(
                spacing=6, tight=True,
                controls=[
                    ft.Icon(ft.Icons.TABLE_VIEW, size=15, color=TEAL_PRIMARY),
                    ft.Text("Excel", size=12, color=TEAL_PRIMARY),
                ],
            ),
            style=ft.ButtonStyle(
                shape=ft.RoundedRectangleBorder(radius=8),
                side=ft.BorderSide(1, TEAL_PRIMARY),
                padding=ft.Padding.symmetric(horizontal=12, vertical=8),
            ),
            on_click=lambda e: _abrir_carpeta(str(Path("output") / "Folios_Datos_Completos.xlsx")),
            tooltip="Abrir Excel de folios procesados",
        )

        # ── Status bar ─────────────────────────────────────
        self.status_icon  = ft.Icon(ft.Icons.CIRCLE, size=12, color=TEXT_MUTED)
        self.status_label = ft.Text("Listo", size=13, color=TEXT_MUTED)
        self.folio_counter = ft.Text("", size=12, color=TEXT_MUTED)
        self.progress_bar  = ft.ProgressBar(
            value=0, bar_height=4,
            color=TEAL_PROGRESS, bgcolor=DIVIDER_COLOR,
            border_radius=ft.BorderRadius.all(4),
            visible=False,
        )

        # ── Área de log ────────────────────────────────────
        self._log_lines: list[str] = []   # cache para copiar
        self.log_column = ft.Column(
            spacing=1, scroll=ft.ScrollMode.AUTO,
            auto_scroll=True, expand=True,
        )

        # ── Resumen ────────────────────────────────────────
        self.resumen_column = ft.Column(spacing=8, visible=False)

        # ── Toast ──────────────────────────────────────────
        self.toast = ft.Container(
            visible=False,
            padding=ft.Padding.symmetric(vertical=8, horizontal=16),
            bgcolor=CARD_BG,
            border=ft.Border.all(1, BORDER_COLOR),
            border_radius=ft.BorderRadius.all(8),
            shadow=ft.BoxShadow(blur_radius=10, color="#22000000", offset=ft.Offset(0, 2)),
            content=ft.Row(
                spacing=8,
                controls=[
                    ft.Icon(ft.Icons.CHECK_CIRCLE, color=GREEN_OK, size=18),
                    ft.Text("", size=13, color=TEXT_DARK),
                ],
            ),
        )

        # ── Historial ──────────────────────────────────────
        self._historial: list = []
        self.hist_column = ft.Column(spacing=8, expand=True, scroll=ft.ScrollMode.AUTO)

        # ── Sidebar toggle btn ─────────────────────────────
        self.btn_toggle_sidebar = ft.IconButton(
            icon=ft.Icons.MENU,
            icon_color=TEXT_GRAY,
            icon_size=20,
            tooltip="Colapsar/expandir menú lateral",
            on_click=self._on_toggle_sidebar,
        )

        # Referencias a contenedores que se reconstruyen
        self.sidebar_container = None
        self.main_area_ref     = None
        self.sidebar_nav_col   = None
        self.header_row        = None
        self.screen_container  = None

        # Registrar atajos de teclado
        page.on_keyboard_event = self._on_keyboard

        # Sincronizar visibilidad inicial según modo guardado
        self._sync_modo_visible()

    # ════════════════════════════════════════════════════
    #  FILEPICKER CALLBACKS
    # ════════════════════════════════════════════════════

    def _on_pick_txt(self, e):
        file_path = _abrir_dialogo_archivo(
            "Selecciona el archivo TXT con folios",
            "Archivos de texto (*.txt)|*.txt|Todos los archivos (*.*)|*.*"
        )
        if file_path:
            self._ruta_txt_interna = file_path
            self.txt_archivo_folios.value = Path(file_path).name
            self.txt_archivo_folios.update()

    def _on_pick_py(self, e):
        file_path = _abrir_dialogo_archivo(
            "Seleccionar ejecutable de Python",
            "Ejecutables (*.exe)|*.exe|Todos los archivos (*.*)|*.*"
        )
        if file_path:
            self.txt_python_path.value = file_path
            self.txt_python_path.update()

    def _on_pick_script(self, e):
        file_path = _abrir_dialogo_archivo(
            "Seleccionar script principal",
            "Scripts de Python (*.py)|*.py|Todos los archivos (*.*)|*.*"
        )
        if file_path:
            self.txt_script_path.value = file_path
            self.txt_script_path.update()

    # ════════════════════════════════════════════════════
    #  ATAJOS DE TECLADO
    # ════════════════════════════════════════════════════

    def _on_keyboard(self, e: ft.KeyboardEvent):
        if e.key == "F5" and not self._running:
            self._on_iniciar(None)
        elif e.key == "Escape" and self._running:
            self._on_detener(None)

    # ════════════════════════════════════════════════════
    #  TOGGLE SIDEBAR
    # ════════════════════════════════════════════════════

    def _on_toggle_sidebar(self, e):
        self._sidebar_visible = not self._sidebar_visible
        if self.sidebar_container:
            self.sidebar_container.visible = self._sidebar_visible
            self.page.update()

    # ════════════════════════════════════════════════════
    #  EVENTOS DE CONTROLES
    # ════════════════════════════════════════════════════

    def _sync_modo_visible(self):
        modo = self.dd_modo.value
        self.folio_rango_row.visible   = (modo == "rango")
        self.folio_manual_row.visible  = (modo == "manual")
        self.folio_archivo_row.visible = (modo == "archivo")
        self.folio_todos_text.visible  = (modo == "todos")

    def _on_modo_change(self, e):
        self._sync_modo_visible()
        self.page.update()

    def _on_limpiar_log(self, e):
        self._log_lines.clear()
        self.log_column.controls.clear()
        self.resumen_column.visible = False
        self.folio_counter.value = ""
        self.page.update()

    def _on_copiar_log(self, e):
        text = "\n".join(self._log_lines)
        try:
            proc = subprocess.Popen(
                ["clip"],
                stdin=subprocess.PIPE,
                close_fds=True,
            )
            proc.communicate(input=text.encode("utf-8"))
            self.show_toast("Log copiado al portapapeles.")
        except Exception as ex:
            self.show_toast(f"No se pudo copiar: {ex}", error=True)

    def _on_iniciar(self, e):
        if self._running:
            return
        self._lanzar_proceso()

    def _on_detener(self, e):
        if self._process:
            try:
                self._process.terminate()
                self._append_log_line("⛔ Proceso detenido por el usuario.", LOG_WARNING)
            except Exception as ex:
                self._append_log_line(f"⚠️ No se pudo detener: {ex}", LOG_ERROR)
        self._set_running(False)

    # ════════════════════════════════════════════════════
    #  LÓGICA DE PROCESO
    # ════════════════════════════════════════════════════

    def _build_args(self) -> list[str]:
        python = self.txt_python_path.value.strip() or _python_exe()
        script = self.txt_script_path.value.strip() or DEFAULT_SCRIPT
        modo   = self.dd_modo.value

        args = [python, "-u", script]   # -u = unbuffered → salida más rápida

        workers_str = self.txt_workers.value.strip()
        if workers_str.isdigit():
            args += ["--workers", workers_str]

        if not self.sw_navegador.value:
            args.append("--headless")

        if modo == "rango":
            ini = self.txt_folio_ini.value.strip()
            fin = self.txt_folio_fin.value.strip()
            if not ini or not fin:
                raise ValueError("Ingresa el folio inicial y final.")
            ini_int, fin_int = int(ini), int(fin)
            if fin_int < ini_int:
                raise ValueError("El folio final no puede ser menor al inicial.")
            cantidad = fin_int - ini_int + 1
            args += ["--buscar", str(cantidad), "--desde", str(ini_int)]

        elif modo == "manual":
            raw = self.txt_folios_manual.value.strip()
            if not raw:
                raise ValueError("Ingresa al menos un folio.")
            folios = [f.strip() for f in raw.replace(",", " ").split() if f.strip()]
            if not folios:
                raise ValueError("No se reconocieron folios válidos.")
            args += folios

        elif modo == "archivo":
            ruta_txt = (getattr(self, "_ruta_txt_interna", "") or self.txt_archivo_folios.value or "").strip()
            if not ruta_txt:
                raise ValueError("Selecciona un archivo TXT con folios.")
            if not Path(ruta_txt).exists():
                raise ValueError(f"El archivo no existe:\n{ruta_txt}")
            args += ["--archivo-folios", ruta_txt]

        # modo "todos": sin folios extra
        return args

    def _lanzar_proceso(self):
        try:
            args = self._build_args()
        except ValueError as ve:
            self.show_toast(str(ve), error=True)
            return

        # Persistir configuración antes de lanzar
        _save_config({
            "folio_ini":    self.txt_folio_ini.value,
            "folio_fin":    self.txt_folio_fin.value,
            "workers":      self.txt_workers.value,
            "modo":         self.dd_modo.value,
            "ver_navegador": self.sw_navegador.value,
            "python_path":  self.txt_python_path.value,
            "script_path":  self.txt_script_path.value,
        })

        self._set_running(True)
        self._resultados = []
        self._folios_procesados = 0
        self.resumen_column.visible = False

        linea_cmd = " ".join(args)
        self._append_log_line(f"{'─' * 60}", LOG_MUTED)
        self._append_log_line(f"[{_ts()}] Iniciando proceso…", LOG_INFO)
        self._append_log_line(f"CMD: {linea_cmd}", LOG_MUTED)
        self._append_log_line(f"{'─' * 60}", LOG_MUTED)
        self.page.update()

        def run():
            try:
                env = os.environ.copy()
                env["PYTHONIOENCODING"] = "utf-8"
                env["PYTHONUNBUFFERED"] = "1"   # sin buffer en el script hijo
                self._process = subprocess.Popen(
                    args,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    env=env,
                    bufsize=1,            # línea por línea — más rápido
                    cwd=str(Path(self.txt_script_path.value).resolve().parent)
                       if Path(self.txt_script_path.value).is_file() else None,
                )
                for line in self._process.stdout:
                    self._log_queue.put(line.rstrip("\n"))
                self._process.wait()
                rc = self._process.returncode
                self._log_queue.put(None)
                self._log_queue.put(("__done__", rc))
            except Exception as ex:
                self._log_queue.put(f"❌ Error al lanzar proceso: {ex}")
                self._log_queue.put(None)
                self._log_queue.put(("__done__", -1))

        def drain():
            """Drena la cola con un timeout corto para mayor responsividad."""
            done = False
            rc = 0
            BATCH = 20          # procesa hasta 20 líneas antes de hacer update
            while True:
                batch = []
                try:
                    while len(batch) < BATCH:
                        item = self._log_queue.get(timeout=0.03)
                        if item is None:
                            done = True
                            break
                        if isinstance(item, tuple) and item[0] == "__done__":
                            rc = item[1]
                            done = True
                            break
                        batch.append(item)
                except queue.Empty:
                    pass

                if batch:
                    for line in batch:
                        color = _color_for_line(line)
                        self._append_log_line(line, color)
                        # Contador de folios en tiempo real
                        if "folio" in line.lower() and ("procesando" in line.lower() or "iniciando" in line.lower()):
                            self._folios_procesados += 1
                            self.folio_counter.value = f"Folios vistos: {self._folios_procesados}"
                    try:
                        self.page.update()
                    except Exception:
                        pass

                if done:
                    break

            try:
                self.page.update()
            except Exception:
                pass
            self._on_proceso_terminado(rc)

        threading.Thread(target=run,   daemon=True).start()
        threading.Thread(target=drain, daemon=True).start()

    def _on_proceso_terminado(self, rc: int):
        ts = _ts()
        if rc == 0:
            self._append_log_line(f"[{ts}] ✅ Proceso completado correctamente.", LOG_SUCCESS)
        else:
            self._append_log_line(f"[{ts}] ❌ Proceso terminó con código {rc}.", LOG_ERROR)

        self._set_running(False)
        try:
            self._cargar_resumen_desde_log()
        except Exception:
            import traceback; traceback.print_exc()
        self._agregar_a_historial(rc)
        try:
            self.page.update()
        except Exception:
            pass

    def _set_running(self, running: bool):
        self._running = running
        self.btn_iniciar.visible = not running
        self.btn_detener.visible = running
        self.progress_bar.visible = running
        if running:
            self.progress_bar.value = None  # indeterminado
            self.status_icon.color  = TEAL_PROGRESS
            self.status_label.value = "Procesando…"
            self.status_label.color = TEAL_PRIMARY
        else:
            self.progress_bar.value = 0
            self.status_icon.color  = TEXT_MUTED
            self.status_label.value = "Listo"
            self.status_label.color = TEXT_MUTED
        try:
            self.page.update()
        except Exception:
            pass

    # ════════════════════════════════════════════════════
    #  LOG
    # ════════════════════════════════════════════════════

    def _append_log_line(self, text: str, color: str = LOG_TEXT):
        self._log_lines.append(text)
        print(text, flush=True)  # Espeja el log en la terminal de VS Code
        self.log_column.controls.append(
            ft.Text(
                text, size=12, color=color,
                font_family="Consolas",
                selectable=True, no_wrap=True,
            )
        )

    # ════════════════════════════════════════════════════
    #  RESUMEN EJECUTIVO
    # ════════════════════════════════════════════════════

    def _cargar_resumen_desde_log(self):
        log_path = DESCARGA_BASE / "procesamiento_log.json"
        if not log_path.exists():
            return
        try:
            data = json.loads(log_path.read_text(encoding="utf-8"))
        except Exception:
            return

        resultados = data.get("resultados", [])
        if not resultados:
            return

        # Mapeo exacto con imprimir_reporte() en main_procesar.py
        exitosos = [
            r for r in resultados
            if r.get("rpc_ok") and r.get("organizado_ok") and r.get("excel_ok")
        ]
        sin_operador = [
            r for r in resultados
            if not r.get("rpc_ok") and not r.get("rpc_resultado")
            and not r.get("organizado_ok")
        ]

        resumen_gral = ft.Container(
            padding=ft.Padding.all(16),
            border_radius=ft.BorderRadius.all(10),
            bgcolor=BLUE_INFO,
            content=ft.Row(
                wrap=True,
                alignment=ft.MainAxisAlignment.SPACE_AROUND,
                controls=[
                    self._stat_col("TOTAL FOLIOS",  len(resultados)),
                    self._stat_col("ÉXITO TOTAL",   len(exitosos)),
                    self._stat_col("SIN CATÁLOGO",  len(sin_operador)),
                ],
            ),
        )

        self.resumen_column.controls = [
            resumen_gral,
            self._resumen_seccion(
                "🟢 ÉXITO TOTAL", exitosos, GREEN_OK,
                lambda r: f"Organizado en: {r.get('rpc_resultado', {}).get('nombre_completo', '—')}",
            ),
            self._resumen_seccion_sin_operador(sin_operador),
        ]

        datos_ui = self._cargar_datos_folios(resultados)
        if datos_ui:
            self.resumen_column.controls.append(datos_ui)

        self.resumen_column.visible = True

    def _cargar_datos_folios(self, resultados) -> ft.Control | None:
        cards = []
        for r in resultados:
            folio   = r.get("folio", "?")
            carpeta = DESCARGA_BASE / folio
            meta    = {}

            for meta_name in ("metadata_satys.json", "metadata_tramite_nuevo.json"):
                mp = carpeta / meta_name
                if mp.exists():
                    try:
                        meta.update(json.loads(mp.read_text(encoding="utf-8")))
                    except Exception:
                        pass

            if not meta:
                continue

            nombre    = meta.get("nombre_operador", meta.get("razon_social", "—"))
            asunto    = meta.get("asunto", "—")
            id_sol    = meta.get("id_solicitante", "—")
            rep_leg   = meta.get("representante_legal", "—")
            fecha_reg = meta.get("fecha_registro", meta.get("fecha", "—"))

            score_rpc = r.get("rpc_resultado", {}).get("score", 0) * 100
            score_str = (
                f"{score_rpc:.0f}%"        if r.get("rpc_ok")
                else ("—"                  if not r.get("pdf_encontrado")
                else f"⚠️ {score_rpc:.0f}%")
            )

            archivos_descargados = []
            if carpeta.exists():
                for f in carpeta.iterdir():
                    if f.is_file() and f.suffix.lower() not in {".json", ".txt"}:
                        archivos_descargados.append(f.name)

            lista_archivos_ui = ft.Column(spacing=2)
            if archivos_descargados:
                lista_archivos_ui.controls.append(
                    ft.Text("Archivos descargados:", size=11, color=TEXT_GRAY, weight=ft.FontWeight.W_600)
                )
                for arch in archivos_descargados:
                    ext = arch.lower()
                    icono = (ft.Icons.PICTURE_AS_PDF if ext.endswith(".pdf")
                             else ft.Icons.TABLE_CHART if ext.endswith(".xlsx")
                             else ft.Icons.INSERT_DRIVE_FILE)
                    color = (RED_ERR if ext.endswith(".pdf")
                             else GREEN_OK if ext.endswith(".xlsx")
                             else TEXT_GRAY)
                    lista_archivos_ui.controls.append(
                        ft.Row(spacing=4, controls=[
                            ft.Icon(icono, size=12, color=color),
                            ft.Text(arch, size=11, color=TEXT_DARK),
                        ])
                    )
            else:
                lista_archivos_ui.controls.append(
                    ft.Text("Sin archivos descargados", size=11, color=TEXT_MUTED, italic=True)
                )

            btn_abrir = ft.TextButton(
                "Abrir carpeta",
                icon=ft.Icons.FOLDER_OPEN,
                style=ft.ButtonStyle(
                    color=TEAL_PRIMARY,
                    padding=ft.Padding.symmetric(horizontal=8, vertical=0),
                ),
                on_click=lambda e, ruta=str(carpeta): _abrir_carpeta(ruta),
            )

            cards.append(
                ft.Container(
                    bgcolor=PAGE_BG, padding=ft.Padding.all(12),
                    border_radius=ft.BorderRadius.all(8),
                    border=ft.Border.all(1, BORDER_COLOR),
                    content=ft.Column(
                        spacing=8,
                        controls=[
                            ft.Row(
                                alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                                controls=[
                                    ft.Text(f"Folio: {folio}", size=14,
                                            weight=ft.FontWeight.W_700, color=TEXT_DARK),
                                    ft.Row(spacing=10, controls=[
                                        btn_abrir,
                                        ft.Container(
                                            padding=ft.Padding.symmetric(horizontal=6, vertical=2),
                                            bgcolor=(TEAL_PRIMARY if r.get("rpc_ok")
                                                     else RED_ERR if not r.get("pdf_encontrado")
                                                     else ORANGE_WARN),
                                            border_radius=ft.BorderRadius.all(6),
                                            content=ft.Text(
                                                f"RPC: {score_str}", size=11,
                                                color="#FFF", weight=ft.FontWeight.W_600,
                                            ),
                                        ),
                                    ]),
                                ],
                            ),
                            ft.Column(
                                spacing=2,
                                controls=[
                                    ft.Text(f"Nombre: {nombre}", size=12,
                                            color=TEXT_DARK, weight=ft.FontWeight.W_600),
                                    ft.Text(f"Asunto: {asunto}", size=11, color=TEXT_GRAY),
                                    ft.Row(
                                        spacing=15,
                                        controls=[
                                            ft.Text(f"ID: {id_sol}", size=11, color=TEXT_GRAY),
                                            ft.Text(f"Rep. Legal: {rep_leg}", size=11, color=TEXT_GRAY),
                                            ft.Text(f"Fecha: {fecha_reg}", size=11, color=TEXT_GRAY),
                                        ],
                                    ),
                                ],
                            ),
                            ft.Divider(height=1, color=DIVIDER_COLOR),
                            lista_archivos_ui,
                        ],
                    ),
                )
            )

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
                            ft.Text("Metadatos extraídos por folio", size=13,
                                    weight=ft.FontWeight.W_700, color=TEXT_DARK),
                        ],
                    ),
                    ft.Column(spacing=8, controls=cards),
                ],
            ),
        )

    def _stat_col(self, title, value):
        return ft.Column(
            horizontal_alignment=ft.CrossAxisAlignment.CENTER, spacing=2,
            controls=[
                ft.Text(str(value), size=24, color="#FFF", weight=ft.FontWeight.W_800),
                ft.Text(title, size=11, color="#E0F7FA", weight=ft.FontWeight.W_600),
            ],
        )

    def _resumen_seccion(self, titulo, items, color, detalle_fn) -> ft.Container:
        count = len(items)
        chips = []
        for r in items:
            folio = r.get("folio", "?")
            det   = detalle_fn(r)
            chips.append(
                ft.Container(
                    padding=ft.Padding.symmetric(horizontal=10, vertical=6),
                    border_radius=ft.BorderRadius.all(6),
                    border=ft.Border.all(1, BORDER_COLOR),
                    bgcolor=PAGE_BG,
                    content=ft.Column(
                        spacing=2,
                        controls=[
                            ft.Text(folio, size=12, weight=ft.FontWeight.W_700, color=TEXT_DARK),
                            ft.Text(det,   size=11, color=TEXT_GRAY),
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
                            ft.Container(width=4, height=20, bgcolor=color,
                                         border_radius=ft.BorderRadius.all(2)),
                            ft.Text(titulo, size=13, weight=ft.FontWeight.W_700, color=TEXT_DARK),
                            ft.Container(
                                padding=ft.Padding.symmetric(horizontal=8, vertical=2),
                                bgcolor=color, border_radius=ft.BorderRadius.all(10),
                                content=ft.Text(str(count), size=11, color="#FFF",
                                                weight=ft.FontWeight.W_700),
                            ),
                        ],
                    ),
                    (ft.Row(wrap=True, spacing=8, run_spacing=8, controls=chips)
                     if chips
                     else ft.Text("Ninguno.", size=12, color=TEXT_MUTED, italic=True)),
                ],
            ),
        )

    def _resumen_seccion_sin_operador(self, items) -> ft.Container:
        """Sección especializada para folios sin operador en catálogo."""
        count  = len(items)
        COLOR  = ORANGE_WARN   # naranja — requiere acción manual
        chips  = []
        for r in items:
            folio  = r.get("folio", "?")
            id_sol = r.get("id_solicitante", "N/A")
            sin_op = r.get("sin_operador_dir",
                           f"output\\_sin_operador\\{folio}")
            chips.append(
                ft.Container(
                    padding=ft.Padding.symmetric(horizontal=12, vertical=8),
                    border_radius=ft.BorderRadius.all(6),
                    border=ft.Border.all(1, BORDER_COLOR),
                    bgcolor=PAGE_BG,
                    content=ft.Column(
                        spacing=3,
                        controls=[
                            ft.Text(
                                f"Folio: {folio}",
                                size=12, weight=ft.FontWeight.W_700, color=TEXT_DARK,
                            ),
                            ft.Text(
                                f"id_solicitante={id_sol} — no encontrado en catálogo RPC",
                                size=11, color=TEXT_GRAY,
                            ),
                            ft.Row(
                                spacing=4,
                                controls=[
                                    ft.Icon(ft.Icons.ARROW_FORWARD, size=11, color=COLOR),
                                    ft.Text(
                                        f"Mueve archivos desde: {sin_op}",
                                        size=10, color=COLOR, italic=True,
                                    ),
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
                            ft.Container(
                                width=4, height=20, bgcolor=COLOR,
                                border_radius=ft.BorderRadius.all(2),
                            ),
                            ft.Text(
                                "📁 SIN OPERADOR EN CATÁLOGO (revisión manual)",
                                size=13, weight=ft.FontWeight.W_700, color=TEXT_DARK,
                            ),
                            ft.Container(
                                padding=ft.Padding.symmetric(horizontal=8, vertical=2),
                                bgcolor=COLOR,
                                border_radius=ft.BorderRadius.all(10),
                                content=ft.Text(
                                    str(count), size=11, color="#FFF",
                                    weight=ft.FontWeight.W_700,
                                ),
                            ),
                        ],
                    ),
                    (
                        ft.Column(spacing=6, controls=chips)
                        if chips
                        else ft.Text("Ninguno.", size=12, color=TEXT_MUTED, italic=True)
                    ),
                ],
            ),
        )

    # ════════════════════════════════════════════════════
    #  HISTORIAL
    # ════════════════════════════════════════════════════

    def _agregar_a_historial(self, rc: int):
        log_path = DESCARGA_BASE / "procesamiento_log.json"
        total = exitosos = 0
        modo  = "—"
        fecha = datetime.now().strftime("%d/%m/%Y %H:%M")

        if log_path.exists():
            try:
                data     = json.loads(log_path.read_text(encoding="utf-8"))
                total    = data.get("total_folios", 0)
                exitosos = data.get("total_exitosos", 0)
                modo     = data.get("modo_extraccion", "—")
            except Exception:
                pass

        self._historial.insert(0, {
            "fecha": fecha, "total": total,
            "exitosos": exitosos, "modo": modo, "rc": rc,
        })
        self._rebuild_historial()

    def _rebuild_historial(self):
        if not self._historial:
            self.hist_column.controls = [
                ft.Container(
                    alignment=ft.Alignment.CENTER,
                    padding=ft.Padding.all(40),
                    content=ft.Column(
                        horizontal_alignment=ft.CrossAxisAlignment.CENTER, spacing=8,
                        controls=[
                            ft.Icon(ft.Icons.HISTORY_TOGGLE_OFF, color=TEXT_MUTED, size=36),
                            ft.Text("Sin ejecuciones en esta sesión", color=TEXT_MUTED, size=13),
                        ],
                    ),
                )
            ]
            return

        rows = []
        for i, h in enumerate(self._historial):
            ok = h["rc"] == 0
            bg = CARD_BG if i % 2 == 0 else "#F8FAFB"
            rows.append(
                ft.Container(
                    bgcolor=bg,
                    padding=ft.Padding.symmetric(horizontal=16, vertical=12),
                    border=ft.Border.only(bottom=ft.BorderSide(1, DIVIDER_COLOR)),
                    content=ft.Row(
                        vertical_alignment=ft.CrossAxisAlignment.CENTER,
                        controls=[
                            ft.Icon(ft.Icons.CHECK_CIRCLE if ok else ft.Icons.CANCEL,
                                    color=GREEN_OK if ok else RED_ERR, size=18),
                            ft.Container(width=10),
                            ft.Container(width=130,
                                content=ft.Text(h["fecha"], size=13, color=TEXT_DARK)),
                            ft.Container(width=90,
                                content=ft.Text(f"{h['exitosos']}/{h['total']} folios",
                                                size=13, color=TEAL_PRIMARY,
                                                weight=ft.FontWeight.W_600)),
                            ft.Container(expand=True,
                                content=ft.Text(f"Extracción: {h['modo']}",
                                                size=12, color=TEXT_GRAY)),
                            ft.Container(
                                padding=ft.Padding.symmetric(horizontal=10, vertical=3),
                                border_radius=ft.BorderRadius.all(10),
                                bgcolor=GREEN_OK if ok else RED_ERR,
                                content=ft.Text("Exitoso" if ok else "Error",
                                                size=11, color="#FFF",
                                                weight=ft.FontWeight.W_600),
                            ),
                        ],
                    ),
                )
            )
        self.hist_column.controls = rows

    # ════════════════════════════════════════════════════
    #  TOAST
    # ════════════════════════════════════════════════════

    def show_toast(self, msg: str, error: bool = False):
        icon = self.toast.content.controls[0]
        text = self.toast.content.controls[1]
        icon.name  = ft.Icons.ERROR_OUTLINE if error else ft.Icons.CHECK_CIRCLE
        icon.color = RED_ERR if error else GREEN_OK
        text.value = msg
        self.toast.visible = True
        self.page.update()
        def _hide():
            time.sleep(2.5)
            self.toast.visible = False
            try:
                self.page.update()
            except Exception:
                pass
        threading.Thread(target=_hide, daemon=True).start()

    # ════════════════════════════════════════════════════
    #  PANTALLA: PROCESAR FOLIOS
    # ════════════════════════════════════════════════════

    def _build_screen_procesar(self) -> ft.Control:

        # ── Tarjeta de configuración ──────────────────────
        config_card = ft.Container(
            bgcolor=CARD_BG,
            border_radius=ft.BorderRadius.all(14),
            padding=ft.Padding.all(20),
            shadow=ft.BoxShadow(blur_radius=16, color="#10000000", offset=ft.Offset(0, 3)),
            content=ft.Column(
                spacing=14,
                controls=[
                    ft.Row(
                        alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                        controls=[
                            ft.Text("Configuración de ejecución", size=15,
                                    weight=ft.FontWeight.W_700, color=TEXT_DARK),
                            # Accesos rápidos a carpetas
                            ft.Row(
                                spacing=8,
                                controls=[
                                    ft.Text("Abrir:", size=12, color=TEXT_MUTED),
                                    self.btn_abrir_descargas,
                                    self.btn_abrir_output,
                                    self.btn_abrir_excel,
                                ],
                            ),
                        ],
                    ),
                    ft.Divider(height=1, color=DIVIDER_COLOR),

                    # Modo + workers + navegador
                    ft.Row(
                        spacing=14,
                        vertical_alignment=ft.CrossAxisAlignment.CENTER,
                        wrap=True,
                        controls=[
                            ft.Text("Modo:", size=13, color=TEXT_GRAY, width=50),
                            self.dd_modo,
                            self.txt_workers,
                            self.sw_navegador,
                        ],
                    ),

                    # Filas dinámicas según modo
                    self.folio_rango_row,
                    self.folio_manual_row,
                    self.folio_archivo_row,
                    self.folio_todos_text,

                    ft.Divider(height=1, color=DIVIDER_COLOR),

                    # Botones + status
                    ft.Row(
                        spacing=12,
                        vertical_alignment=ft.CrossAxisAlignment.CENTER,
                        controls=[
                            self.btn_iniciar,
                            self.btn_detener,
                            ft.Container(expand=True),
                            self.status_icon,
                            self.status_label,
                            self.folio_counter,
                        ],
                    ),
                    self.progress_bar,
                ],
            ),
        )

        # ── Tarjeta de log ────────────────────────────────
        log_card = ft.Container(
            height=300,
            bgcolor=CARD_BG,
            border_radius=ft.BorderRadius.all(14),
            shadow=ft.BoxShadow(blur_radius=16, color="#10000000", offset=ft.Offset(0, 3)),
            padding=ft.Padding.all(0),
            clip_behavior=ft.ClipBehavior.ANTI_ALIAS,
            content=ft.Column(
                spacing=0, expand=True,
                controls=[
                    ft.Container(
                        bgcolor=TABLE_HEADER_BG,
                        padding=ft.Padding.symmetric(horizontal=16, vertical=10),
                        border=ft.Border.only(bottom=ft.BorderSide(1, BORDER_COLOR)),
                        content=ft.Row(
                            vertical_alignment=ft.CrossAxisAlignment.CENTER,
                            controls=[
                                ft.Icon(ft.Icons.TERMINAL, size=16, color=TEXT_GRAY),
                                ft.Container(width=6),
                                ft.Text("Log del proceso", size=13,
                                        weight=ft.FontWeight.W_600, color=TEXT_DARK),
                                ft.Container(expand=True),
                                self.btn_copiar_log,
                                self.btn_limpiar,
                            ],
                        ),
                    ),
                    ft.Container(
                        expand=True, bgcolor=LOG_BG,
                        padding=ft.Padding.all(14),
                        content=self.log_column,
                    ),
                ],
            ),
        )

        # ── Tarjeta de resumen ────────────────────────────
        resumen_card = ft.Container(
            bgcolor=CARD_BG,
            border_radius=ft.BorderRadius.all(14),
            padding=ft.Padding.all(20),
            shadow=ft.BoxShadow(blur_radius=16, color="#10000000", offset=ft.Offset(0, 3)),
            content=ft.Column(
                spacing=12,
                controls=[
                    ft.Row(
                        vertical_alignment=ft.CrossAxisAlignment.CENTER,
                        controls=[
                            ft.Icon(ft.Icons.SUMMARIZE_OUTLINED, size=18, color=TEAL_PRIMARY),
                            ft.Container(width=8),
                            ft.Text("Resumen ejecutivo", size=15,
                                    weight=ft.FontWeight.W_700, color=TEXT_DARK),
                        ],
                    ),
                    ft.Divider(height=1, color=DIVIDER_COLOR),
                    self.resumen_column,
                ],
            ),
        )

        return ft.Column(
            spacing=16, expand=True, scroll=ft.ScrollMode.AUTO,
            controls=[config_card, log_card, resumen_card],
        )

    # ════════════════════════════════════════════════════
    #  PANTALLA: HISTORIAL
    # ════════════════════════════════════════════════════

    def _build_screen_historial(self) -> ft.Control:
        self._rebuild_historial()
        header = ft.Container(
            bgcolor=TABLE_HEADER_BG,
            padding=ft.Padding.symmetric(horizontal=16, vertical=10),
            border_radius=ft.BorderRadius.only(top_left=10, top_right=10),
            border=ft.Border.only(bottom=ft.BorderSide(1, BORDER_COLOR)),
            content=ft.Row(
                controls=[
                    ft.Container(width=28),
                    ft.Container(width=130, content=ft.Text("Fecha", size=12, color=TEXT_GRAY, weight=ft.FontWeight.W_700)),
                    ft.Container(width=90,  content=ft.Text("Folios", size=12, color=TEXT_GRAY, weight=ft.FontWeight.W_700)),
                    ft.Container(expand=True, content=ft.Text("Modo extracción", size=12, color=TEXT_GRAY, weight=ft.FontWeight.W_700)),
                    ft.Text("Estado", size=12, color=TEXT_GRAY, weight=ft.FontWeight.W_700),
                ],
            ),
        )
        return ft.Container(
            expand=True, bgcolor=CARD_BG,
            border_radius=ft.BorderRadius.all(14),
            padding=ft.Padding.all(0),
            shadow=ft.BoxShadow(blur_radius=16, color="#10000000", offset=ft.Offset(0, 3)),
            clip_behavior=ft.ClipBehavior.ANTI_ALIAS,
            content=ft.Column(
                spacing=0, expand=True,
                controls=[
                    ft.Container(
                        padding=ft.Padding.symmetric(horizontal=20, vertical=16),
                        border=ft.Border.only(bottom=ft.BorderSide(1, BORDER_COLOR)),
                        content=ft.Row(
                            vertical_alignment=ft.CrossAxisAlignment.CENTER,
                            controls=[
                                ft.Icon(ft.Icons.HISTORY, size=18, color=TEAL_PRIMARY),
                                ft.Container(width=8),
                                ft.Text("Historial de ejecuciones", size=15,
                                        weight=ft.FontWeight.W_700, color=TEXT_DARK),
                            ],
                        ),
                    ),
                    header,
                    self.hist_column,
                ],
            ),
        )

    # ════════════════════════════════════════════════════
    #  PANTALLA: CONFIGURACIÓN
    # ════════════════════════════════════════════════════

    def _build_screen_config(self) -> ft.Control:

        def _field_row(label, ctrl_row, hint=""):
            return ft.Column(
                spacing=6,
                controls=[
                    ft.Text(label, size=12, color=TEXT_GRAY, weight=ft.FontWeight.W_600),
                    ctrl_row,
                    ft.Text(hint, size=11, color=TEXT_MUTED, italic=True) if hint else ft.Container(height=0),
                ],
            )

        # ── Fila de Python con botón Examinar ─────────────
        row_python = ft.Row(
            spacing=8,
            controls=[
                self.txt_python_path,
                ft.Button(
                    content=ft.Row(
                        spacing=6, tight=True,
                        controls=[
                            ft.Icon(ft.Icons.FOLDER_OPEN, size=15, color="#FFF"),
                            ft.Text("Examinar", size=12, color="#FFF"),
                        ],
                    ),
                    bgcolor=TEAL_PRIMARY,
                    style=ft.ButtonStyle(
                        shape=ft.RoundedRectangleBorder(radius=8),
                        padding=ft.Padding.symmetric(horizontal=14, vertical=10),
                        elevation=0,
                    ),
                    on_click=self._on_pick_py,
                ),
            ],
        )

        # ── Fila de script con botón Examinar ─────────────
        row_script = ft.Row(
            spacing=8,
            controls=[
                self.txt_script_path,
                ft.Button(
                    content=ft.Row(
                        spacing=6, tight=True,
                        controls=[
                            ft.Icon(ft.Icons.FOLDER_OPEN, size=15, color="#FFF"),
                            ft.Text("Examinar", size=12, color="#FFF"),
                        ],
                    ),
                    bgcolor=TEAL_PRIMARY,
                    style=ft.ButtonStyle(
                        shape=ft.RoundedRectangleBorder(radius=8),
                        padding=ft.Padding.symmetric(horizontal=14, vertical=10),
                        elevation=0,
                    ),
                    on_click=self._on_pick_script,
                ),
            ],
        )

        def _guardar(e):
            _save_config({
                "folio_ini":    self.txt_folio_ini.value,
                "folio_fin":    self.txt_folio_fin.value,
                "workers":      self.txt_workers.value,
                "modo":         self.dd_modo.value,
                "ver_navegador": self.sw_navegador.value,
                "python_path":  self.txt_python_path.value,
                "script_path":  self.txt_script_path.value,
            })
            self.show_toast("✅ Configuración guardada en config_satys.json")

        return ft.Container(
            bgcolor=CARD_BG,
            border_radius=ft.BorderRadius.all(14),
            padding=ft.Padding.all(24),
            shadow=ft.BoxShadow(blur_radius=16, color="#10000000", offset=ft.Offset(0, 3)),
            content=ft.Column(
                spacing=20,
                controls=[
                    ft.Text("Configuración del entorno", size=15,
                            weight=ft.FontWeight.W_700, color=TEXT_DARK),
                    ft.Divider(height=1, color=DIVIDER_COLOR),

                    _field_row(
                        "Ejecutable de Python",
                        row_python,
                        r"Ej: python_portable\python.exe  ó  C:\Python312\python.exe",
                    ),
                    _field_row(
                        "Script principal",
                        row_script,
                        "Ruta al archivo main_procesar.py",
                    ),

                    ft.Divider(height=1, color=DIVIDER_COLOR),

                    ft.Container(
                        bgcolor=PAGE_BG,
                        border_radius=ft.BorderRadius.all(10),
                        padding=ft.Padding.all(14),
                        content=ft.Column(
                            spacing=6,
                            controls=[
                                ft.Row(
                                    spacing=8,
                                    controls=[
                                        ft.Icon(ft.Icons.INFO_OUTLINE, size=16, color=BLUE_INFO),
                                        ft.Text("Credenciales", size=13,
                                                weight=ft.FontWeight.W_600, color=TEXT_DARK),
                                    ],
                                ),
                                ft.Text(
                                    "Las credenciales de SATyS (SATYS_USER / SATYS_PASS) y Azure AI "
                                    "(AZURE_DOCUMENT_INTELLIGENCE_KEY) se leen de variables de entorno "
                                    "o del archivo .env en el directorio del proyecto.",
                                    size=12, color=TEXT_GRAY,
                                ),
                            ],
                        ),
                    ),

                    ft.Container(
                        bgcolor="#FFF8E1",
                        border_radius=ft.BorderRadius.all(10),
                        border=ft.Border.all(1, "#FFECB3"),
                        padding=ft.Padding.all(14),
                        content=ft.Row(
                            spacing=8,
                            controls=[
                                ft.Icon(ft.Icons.KEYBOARD, size=16, color=ORANGE_WARN),
                                ft.Text(
                                    "Atajos: F5 = Iniciar proceso  ·  Esc = Detener proceso",
                                    size=12, color=ORANGE_WARN, weight=ft.FontWeight.W_600,
                                ),
                            ],
                        ),
                    ),

                    ft.Button(
                        "Guardar configuración",
                        icon=ft.Icons.SAVE_OUTLINED,
                        bgcolor=TEAL_PRIMARY, color="#FFF",
                        style=ft.ButtonStyle(
                            shape=ft.RoundedRectangleBorder(radius=8),
                            elevation=0,
                        ),
                        on_click=_guardar,
                    ),
                ],
            ),
        )

    # ════════════════════════════════════════════════════
    #  BARRA LATERAL
    # ════════════════════════════════════════════════════

    def _build_nav_item(self, label: str, icon_out, icon_fill) -> ft.Container:
        active = label == self.active_nav

        def on_click(e):
            self.active_nav = label
            self._rebuild_sidebar()
            self._switch_screen(label)
            self.page.update()

        return ft.Container(
            padding=ft.Padding.symmetric(horizontal=14, vertical=11),
            margin=ft.Margin.only(left=10, right=10, bottom=4),
            border_radius=ft.BorderRadius.all(8),
            bgcolor=SIDEBAR_SEL_BG if active else None,
            ink=True,
            on_click=on_click,
            content=ft.Row(
                spacing=12,
                controls=[
                    ft.Icon(icon_fill if active else icon_out, size=19,
                            color=TEAL_DARK if active else TEXT_GRAY),
                    ft.Text(label, size=13.5,
                            color=TEAL_DARK if active else TEXT_GRAY,
                            weight=ft.FontWeight.W_600 if active else ft.FontWeight.W_400),
                ],
            ),
        )

    def _rebuild_sidebar(self):
        self.sidebar_nav_col.controls = [
            self._build_nav_item(*item) for item in NAV_ITEMS
        ]

    def _build_sidebar(self) -> ft.Container:
        self.sidebar_nav_col = ft.Column(spacing=0)
        self._rebuild_sidebar()

        self.sidebar_container = ft.Container(
            width=230,
            bgcolor=SIDEBAR_BG,
            content=ft.Column(
                controls=[
                    ft.Container(
                        padding=ft.Padding.only(left=18, right=18, top=22, bottom=10),
                        content=ft.Image(src="logo.png", width=150, fit=ft.BoxFit.CONTAIN),
                    ),
                    ft.Divider(height=1, color="#BDD0D1"),
                    ft.Container(height=8),
                    ft.Container(
                        padding=ft.Padding.symmetric(horizontal=16, vertical=4),
                        content=ft.Text("AUTOMATIZACIÓN SATyS", size=10,
                                        color=TEXT_MUTED, weight=ft.FontWeight.W_700),
                    ),
                    ft.Container(height=4),
                    self.sidebar_nav_col,
                    ft.Container(expand=True),
                    ft.Container(
                        padding=ft.Padding.only(left=16, right=16, bottom=16),
                        content=ft.Text("© CRT 2025", size=11, color=TEXT_MUTED),
                    ),
                ],
            ),
        )
        return self.sidebar_container

    # ════════════════════════════════════════════════════
    #  ENCABEZADO
    # ════════════════════════════════════════════════════

    def _build_header(self) -> ft.Row:
        screen_titles = {
            "Procesar Folios": "Gestor de Automatización",
            "Historial":       "Historial de Ejecuciones",
            "Configuración":   "Configuración del Entorno",
        }
        return ft.Row(
            alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
            vertical_alignment=ft.CrossAxisAlignment.START,
            controls=[
                ft.Row(
                    spacing=10,
                    vertical_alignment=ft.CrossAxisAlignment.CENTER,
                    controls=[
                        self.btn_toggle_sidebar,
                        ft.Column(
                            spacing=2,
                            controls=[
                                ft.Text("SATyS — Control de Descarga y Procesamiento",
                                        size=13, color=TEXT_GRAY),
                                ft.Text(screen_titles.get(self.active_nav, ""),
                                        size=22, color=TEXT_DARK, weight=ft.FontWeight.W_700),
                            ],
                        ),
                    ],
                ),
                ft.Row(
                    spacing=10,
                    vertical_alignment=ft.CrossAxisAlignment.CENTER,
                    controls=[
                        ft.CircleAvatar(
                            content=ft.Icon(ft.Icons.PERSON, color="#9AA3A6"),
                            bgcolor="#E4E7E9", radius=18,
                        ),
                        ft.Text("CRT Usuario", size=13, color=TEXT_DARK,
                                weight=ft.FontWeight.W_600),
                    ],
                ),
            ],
        )

    # ════════════════════════════════════════════════════
    #  SWITCH DE PANTALLAS
    # ════════════════════════════════════════════════════

    def _switch_screen(self, label: str):
        screens = {
            "Procesar Folios": self._build_screen_procesar,
            "Historial":       self._build_screen_historial,
            "Configuración":   self._build_screen_config,
        }
        builder = screens.get(label, self._build_screen_procesar)
        self.header_row.controls[0].controls[1].controls[1].value = {
            "Procesar Folios": "Gestor de Automatización",
            "Historial":       "Historial de Ejecuciones",
            "Configuración":   "Configuración del Entorno",
        }.get(label, "")
        self.screen_container.content = builder()

    # ════════════════════════════════════════════════════
    #  BUILD COMPLETO
    # ════════════════════════════════════════════════════

    def build(self) -> ft.Stack:
        sidebar = self._build_sidebar()

        self.header_row = self._build_header()
        self.screen_container = ft.Container(
            expand=True,
            content=self._build_screen_procesar(),
        )

        main_area = ft.Container(
            expand=True, bgcolor=PAGE_BG,
            padding=ft.Padding.only(left=28, right=28, top=22, bottom=14),
            content=ft.Column(
                spacing=18, expand=True,
                controls=[
                    self.header_row,
                    self.screen_container,
                    ft.Text(
                        "Optimizado para Windows  ·  © Comisión Reguladora de Telecomunicaciones 2025",
                        size=11, color=TEXT_MUTED, text_align=ft.TextAlign.CENTER,
                    ),
                ],
            ),
        )

        return ft.Stack(
            expand=True,
            controls=[
                ft.Row(
                    expand=True, spacing=0,
                    controls=[sidebar, main_area],
                ),
                ft.Container(top=16, right=16, content=self.toast),
            ],
        )


# ════════════════════════════════════════════════════════
#  ENTRY POINT
# ════════════════════════════════════════════════════════

def main(page: ft.Page):
    page.title   = "SATyS — Gestor de Automatización CRT"
    page.bgcolor = PAGE_BG
    page.padding = 0

    # Ventana más grande por defecto
    page.window.width      = 1400
    page.window.height     = 860
    page.window.min_width  = 1000
    page.window.min_height = 650
    page.window.resizable  = True
    page.window.maximizable = True

    page.theme = ft.Theme(
        font_family="Segoe UI",
        color_scheme_seed=TEAL_PRIMARY,
    )

    app = SATySApp(page)
    page.add(app.build())


if __name__ == "__main__":
    ft.run(main, assets_dir="assets", view=ft.AppView.WEB_BROWSER)