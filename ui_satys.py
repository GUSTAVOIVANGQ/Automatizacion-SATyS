#!/usr/bin/env python3
"""
=============================================================
  UI — GESTOR DE AUTOMATIZACIÓN SATyS / CRT
=============================================================
Interfaz gráfica en Flet para controlar main_procesar.py.

Estructura:
  - Barra lateral: navegación entre secciones
  - Panel Principal: configuración y ejecución del proceso
  - Log en tiempo real con colores y scroll automático
  - Resumen ejecutivo al finalizar

Uso:
  python ui_satys.py
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
#  PALETA DE COLORES (basada en el logo CRT)
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
#  CONFIGURACIÓN POR DEFECTO
# ════════════════════════════════════════════════════════

DEFAULT_SCRIPT     = "main_procesar.py"
DEFAULT_PYTHON     = r"python_portable\python.exe"
DEFAULT_FOLIO_INI  = "6407"
DEFAULT_FOLIO_FIN  = "6433"
DESCARGA_BASE      = Path("descargas")

# Pantallas de la navegación lateral
NAV_ITEMS = [
    ("Procesar Folios",    ft.Icons.PLAY_CIRCLE_OUTLINE,  ft.Icons.PLAY_CIRCLE),
    ("Historial",          ft.Icons.HISTORY,               ft.Icons.HISTORY),
    ("Configuración",      ft.Icons.SETTINGS_OUTLINED,     ft.Icons.SETTINGS),
]


# ════════════════════════════════════════════════════════
#  HELPERS
# ════════════════════════════════════════════════════════

def _color_for_line(line: str) -> str:
    """Determina el color del texto de log según su contenido."""
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
    """Devuelve el ejecutable Python a usar."""
    portable = Path(DEFAULT_PYTHON)
    if portable.exists():
        return str(portable)
    return sys.executable


# ════════════════════════════════════════════════════════
#  CLASE PRINCIPAL DE LA APLICACIÓN
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

        # ── Controles de configuración ──────────────────────
        self.txt_folio_ini = ft.TextField(
            value=DEFAULT_FOLIO_INI, width=120,
            text_size=14, height=40,
            border_color=BORDER_COLOR,
            focused_border_color=TEAL_PRIMARY,
            border_radius=ft.BorderRadius.all(8),
            content_padding=ft.Padding.symmetric(vertical=6, horizontal=10),
        )
        self.txt_folio_fin = ft.TextField(
            value=DEFAULT_FOLIO_FIN, width=120,
            text_size=14, height=40,
            border_color=BORDER_COLOR,
            focused_border_color=TEAL_PRIMARY,
            border_radius=ft.BorderRadius.all(8),
            content_padding=ft.Padding.symmetric(vertical=6, horizontal=10),
        )
        self.txt_folios_manual = ft.TextField(
            hint_text="Ej: 6407, 6801, 6802",
            hint_style=ft.TextStyle(color=TEXT_MUTED, size=13),
            text_size=13, height=40, expand=True,
            border_color=BORDER_COLOR,
            focused_border_color=TEAL_PRIMARY,
            border_radius=ft.BorderRadius.all(8),
            content_padding=ft.Padding.symmetric(vertical=6, horizontal=10),
        )

        self.dd_modo = ft.Dropdown(
            value="rango",
            options=[
                ft.dropdown.Option("rango", "Rango de folios"),
                ft.dropdown.Option("manual", "Folios específicos"),
                ft.dropdown.Option("archivo", "Archivo TXT de folios"),
                ft.dropdown.Option("todos", "Todos en carpeta descargas/"),
            ],
            width=220, text_size=13, height=42,
            border_color=BORDER_COLOR,
            focused_border_color=TEAL_PRIMARY,
            border_radius=ft.BorderRadius.all(8),
            content_padding=ft.Padding.symmetric(vertical=4, horizontal=10), on_select=self._on_modo_change,
        )

        self.folio_rango_row = ft.Row(
            spacing=10,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
            controls=[
                ft.Text("Desde:", size=13, color=TEXT_GRAY),
                self.txt_folio_ini,
                ft.Text("Hasta:", size=13, color=TEXT_GRAY),
                self.txt_folio_fin,
            ],
        )
        self.folio_manual_row = ft.Row(
            spacing=10,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
            visible=False,
            controls=[
                ft.Text("Folios:", size=13, color=TEXT_GRAY),
                self.txt_folios_manual,
            ],
        )
        self.txt_archivo_folios = ft.TextField(
            hint_text="Ruta del archivo .txt (ej: folios.txt)",
            text_size=13, height=40, expand=True,
            border_color=BORDER_COLOR,
            focused_border_color=TEAL_PRIMARY,
            border_radius=ft.BorderRadius.all(8),
            content_padding=ft.Padding.symmetric(vertical=6, horizontal=10),
        )
        self.folio_archivo_row = ft.Row(
            spacing=10,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
            visible=False,
            controls=[
                ft.Text("Archivo:", size=13, color=TEXT_GRAY),
                self.txt_archivo_folios,
            ],
        )

        self.txt_workers = ft.TextField(
            value="10", width=160,
            text_size=13, height=40,
            label="Ventanas Playwright",
            border_color=BORDER_COLOR,
            focused_border_color=TEAL_PRIMARY,
            border_radius=ft.BorderRadius.all(8),
            content_padding=ft.Padding.symmetric(vertical=6, horizontal=10),
        )
        self.sw_navegador = ft.Switch(
            label="Mostrar navegador",
            value=True,
            active_color=TEAL_PRIMARY,
        )

        self.folio_todos_text = ft.Text(
            "Se procesarán todas las carpetas dentro de descargas/",
            size=13, color=TEXT_MUTED, italic=True, visible=False,
        )

        # ── Botones principales ──────────────────────────────
        self.btn_iniciar = ft.ElevatedButton(
            content=ft.Row(
                spacing=8, tight=True,
                controls=[
                    ft.Icon(ft.Icons.PLAY_ARROW, size=18, color="#FFF"),
                    ft.Text("Iniciar Proceso", size=14, color="#FFF", weight=ft.FontWeight.W_600),
                ],
            ),
            bgcolor=TEAL_PRIMARY,
            style=ft.ButtonStyle(
                shape=ft.RoundedRectangleBorder(radius=10),
                padding=ft.Padding.symmetric(horizontal=22, vertical=14),
                elevation=0,
            ),
            on_click=self._on_iniciar,
        )
        self.btn_detener = ft.ElevatedButton(
            content=ft.Row(
                spacing=8, tight=True,
                controls=[
                    ft.Icon(ft.Icons.STOP, size=18, color="#FFF"),
                    ft.Text("Detener", size=14, color="#FFF", weight=ft.FontWeight.W_600),
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

        # ── Status badge ────────────────────────────────────
        self.status_icon  = ft.Icon(ft.Icons.CIRCLE, size=12, color=TEXT_MUTED)
        self.status_label = ft.Text("Listo", size=13, color=TEXT_MUTED)
        self.progress_bar = ft.ProgressBar(
            value=0, bar_height=4,
            color=TEAL_PROGRESS, bgcolor=DIVIDER_COLOR,
            border_radius=ft.BorderRadius.all(4),
            visible=False,
        )

        # ── Área de log ──────────────────────────────────────
        self.log_column = ft.Column(
            spacing=1, scroll=ft.ScrollMode.AUTO,
            auto_scroll=True,
            expand=True,
        )

        # ── Resumen ─────────────────────────────────────────
        self.resumen_column = ft.Column(spacing=8, visible=False)

        # ── Toast ───────────────────────────────────────────
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

        # ── Historial (en memoria) ───────────────────────────
        self._historial: list = []  # lista de dict con resúmenes de ejecuciones
        self.hist_column = ft.Column(spacing=8, expand=True, scroll=ft.ScrollMode.AUTO)

        # ── Configuración persistida ─────────────────────────
        self.txt_python_path = ft.TextField(
            value=_python_exe(),
            text_size=13, height=40, expand=True,
            border_color=BORDER_COLOR,
            focused_border_color=TEAL_PRIMARY,
            border_radius=ft.BorderRadius.all(8),
            content_padding=ft.Padding.symmetric(vertical=6, horizontal=10),
        )
        self.txt_script_path = ft.TextField(
            value=DEFAULT_SCRIPT,
            text_size=13, height=40, expand=True,
            border_color=BORDER_COLOR,
            focused_border_color=TEAL_PRIMARY,
            border_radius=ft.BorderRadius.all(8),
            content_padding=ft.Padding.symmetric(vertical=6, horizontal=10),
        )

        # Referencia a la vista principal (se llena en build())
        self.main_content_ref = None
        self.sidebar_nav_col = None

    # ════════════════════════════════════════════════════
    #  EVENTOS DE CONTROLES
    # ════════════════════════════════════════════════════

    def _on_modo_change(self, e):
        modo = self.dd_modo.value
        self.folio_rango_row.visible  = (modo == "rango")
        self.folio_manual_row.visible = (modo == "manual")
        self.folio_archivo_row.visible = (modo == "archivo")
        self.folio_todos_text.visible = (modo == "todos")
        self.page.update()



    def _on_limpiar_log(self, e):
        self.log_column.controls.clear()
        self.resumen_column.visible = False
        self.page.update()

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
        """Construye los argumentos para main_procesar.py."""
        python  = self.txt_python_path.value.strip() or _python_exe()
        script  = self.txt_script_path.value.strip() or DEFAULT_SCRIPT
        modo    = self.dd_modo.value

        args = [python, script]



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
            ruta_txt = self.txt_archivo_folios.value.strip()
            if not ruta_txt:
                raise ValueError("Selecciona un archivo TXT con folios.")
            if not Path(ruta_txt).exists():
                raise ValueError("El archivo TXT seleccionado no existe.")
            args += ["--archivo-folios", ruta_txt]

        # modo "todos": --solo-procesar ya agregado, sin folios extra

        return args

    def _lanzar_proceso(self):
        try:
            args = self._build_args()
        except ValueError as ve:
            self.show_toast(str(ve), error=True)
            return

        self._set_running(True)
        self._resultados = []
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
                env["PYTHONUNBUFFERED"] = "1"
                self._process = subprocess.Popen(
                    args,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    env=env,
                    cwd=str(Path(self.txt_script_path.value).resolve().parent)
                       if Path(self.txt_script_path.value).is_file() else None,
                )
                for line in self._process.stdout:
                    line = line.rstrip("\n")
                    self._log_queue.put(line)
                self._process.wait()
                rc = self._process.returncode
                self._log_queue.put(None)  # señal de fin
                self._log_queue.put(("__done__", rc))
            except Exception as ex:
                self._log_queue.put(f"❌ Error al lanzar proceso: {ex}")
                self._log_queue.put(None)
                self._log_queue.put(("__done__", -1))

        def drain():
            """Drena la cola de log en el hilo de la UI."""
            done = False
            rc = 0
            while True:
                try:
                    item = self._log_queue.get(timeout=0.05)
                except queue.Empty:
                    if done:
                        break
                    try:
                        self.page.update()
                    except Exception:
                        pass
                    continue

                if item is None:
                    done = True
                elif isinstance(item, tuple) and item[0] == "__done__":
                    rc = item[1]
                else:
                    self._append_log_line(str(item), _color_for_line(str(item)))

            # Fin del proceso
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
        except Exception as e:
            import traceback
            traceback.print_exc()
            
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
        self.log_column.controls.append(
            ft.Text(
                text,
                size=12,
                color=color,
                font_family="Consolas",
                selectable=True,
                no_wrap=True,
            )
        )

    # ════════════════════════════════════════════════════
    #  RESUMEN EJECUTIVO
    # ════════════════════════════════════════════════════

    def _cargar_resumen_desde_log(self):
        """Lee descargas/procesamiento_log.json si existe y genera resumen visual."""
        log_path = DESCARGA_BASE / "procesamiento_log.json"
        if not log_path.exists():
            return

        try:
            with open(log_path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            return

        resultados = data.get("resultados", [])
        if not resultados:
            return

        exitosos = [r for r in resultados if r.get("rpc_ok") and r.get("excel_ok")]
        empates  = [r for r in resultados if r.get("rpc_resultado", {}).get("empate")]
        dudosos  = [r for r in resultados if r.get("pdf_encontrado") and not r.get("rpc_ok")
                    and not r.get("rpc_resultado", {}).get("empate")]
        errores  = [r for r in resultados if not r.get("pdf_encontrado")]

        total_archivos = 0
        for r in resultados:
            folio = r.get("folio", "")
            if folio:
                c = DESCARGA_BASE / folio
                if c.exists():
                    total_archivos += len([f for f in c.iterdir() if f.is_file() and not f.name.endswith('.json') and not f.name.endswith('.txt')])
        
        resumen_gral = ft.Container(
            padding=ft.Padding.all(16),
            border_radius=ft.BorderRadius.all(10),
            bgcolor=BLUE_INFO,
            content=ft.Row(
                wrap=True,
                alignment=ft.MainAxisAlignment.SPACE_AROUND,
                controls=[
                    self._stat_col("TOTAL FOLIOS", len(resultados)),
                    self._stat_col("EXITOSOS", len(exitosos)),
                    self._stat_col("DUPLICADOS", len(empates)),
                    self._stat_col("INCOMPLETOS", len(dudosos)),
                    self._stat_col("NO ENCONTRADOS", len(errores)),
                    self._stat_col("ARCH. DESCARGADOS", total_archivos),
                ]
            )
        )

        self.resumen_column.controls = [
            resumen_gral,
            self._resumen_seccion("🟢 Éxito total", exitosos, GREEN_OK,
                lambda r: r.get("rpc_resultado", {}).get("nombre_completo", "—")),
            self._resumen_seccion("🟠 Duplicados en RPC (revisión manual)", empates, ORANGE_WARN,
                lambda r: r.get("rpc_resultado", {}).get("nombre_completo", "—")),
            self._resumen_seccion("🟡 Coincidencia baja (revisión manual)", dudosos, "#C4A020",
                lambda r: f"{r.get('nombre_operador','—')} · {r.get('rpc_resultado',{}).get('score',0)*100:.0f}%"),
            self._resumen_seccion("🔴 Errores", errores, RED_ERR,
                lambda r: "PDF no descargado"),
        ]

        datos_ui = self._cargar_datos_folios(resultados)
        if datos_ui:
            self.resumen_column.controls.append(datos_ui)

        self.resumen_column.visible = True

        self.resumen_column.visible = True

    def _cargar_datos_folios(self, resultados) -> ft.Control | None:
        cards = []
        for r in resultados:
            folio = r.get("folio", "?")
            carpeta = DESCARGA_BASE / folio
            meta = {}
            
            meta_path = carpeta / "metadata_satys.json"
            if meta_path.exists():
                try:
                    with open(meta_path, "r", encoding="utf-8") as fm:
                        meta = json.load(fm)
                except Exception:
                    pass
            
            meta_nuevo_path = carpeta / "metadata_tramite_nuevo.json"
            if meta_nuevo_path.exists():
                try:
                    with open(meta_nuevo_path, "r", encoding="utf-8") as fmn:
                        meta.update(json.load(fmn))
                except Exception:
                    pass

            if not meta:
                continue

            nombre = meta.get("nombre_operador", meta.get("razon_social", "—"))
            asunto = meta.get("asunto", "—")
            id_sol = meta.get("id_solicitante", "—")
            rep_leg = meta.get("representante_legal", "—")
            fecha_reg = meta.get("fecha_registro", meta.get("fecha", "—"))
            
            score_rpc = r.get("rpc_resultado", {}).get("score", 0) * 100
            score_str = f"{score_rpc:.0f}%" if r.get("rpc_ok") else ("—" if not r.get("pdf_encontrado") else f"⚠️ {score_rpc:.0f}%")

            archivos_descargados = []
            if carpeta.exists():
                for f in carpeta.iterdir():
                    if f.is_file() and f.suffix.lower() not in [".json", ".txt"]:
                        archivos_descargados.append(f.name)

            lista_archivos_ui = ft.Column(spacing=2)
            if archivos_descargados:
                lista_archivos_ui.controls.append(ft.Text("Archivos descargados:", size=11, color=TEXT_GRAY, weight=ft.FontWeight.W_600))
                for arch in archivos_descargados:
                    icono = ft.Icons.PICTURE_AS_PDF if arch.lower().endswith(".pdf") else (ft.Icons.TABLE_CHART if arch.lower().endswith(".xlsx") else ft.Icons.INSERT_DRIVE_FILE)
                    color = RED_ERR if arch.lower().endswith(".pdf") else (GREEN_OK if arch.lower().endswith(".xlsx") else TEXT_GRAY)
                    lista_archivos_ui.controls.append(
                        ft.Row(spacing=4, controls=[
                            ft.Icon(icono, size=12, color=color),
                            ft.Text(arch, size=11, color=TEXT_DARK)
                        ])
                    )
            else:
                lista_archivos_ui.controls.append(ft.Text("Sin archivos descargados", size=11, color=TEXT_MUTED, italic=True))

            btn_abrir = ft.TextButton(
                "Abrir carpeta",
                icon=ft.Icons.FOLDER_OPEN,
                style=ft.ButtonStyle(color=TEAL_PRIMARY, padding=ft.Padding.symmetric(horizontal=8, vertical=0)),
                on_click=lambda e, ruta=str(carpeta): os.startfile(ruta) if os.path.exists(ruta) else None
            )

            cards.append(
                ft.Container(
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
                                    ft.Text(f"Folio: {folio}", size=14, weight=ft.FontWeight.W_700, color=TEXT_DARK),
                                    ft.Row(spacing=10, controls=[
                                        btn_abrir,
                                        ft.Container(
                                            padding=ft.Padding.symmetric(horizontal=6, vertical=2),
                                            bgcolor=TEAL_PRIMARY if r.get("rpc_ok") else (RED_ERR if not r.get("pdf_encontrado") else ORANGE_WARN),
                                            border_radius=ft.BorderRadius.all(6),
                                            content=ft.Text(f"RPC Exactitud: {score_str}", size=11, color="#FFF", weight=ft.FontWeight.W_600),
                                        )
                                    ])
                                ]
                            ),
                            ft.Column(
                                spacing=2,
                                controls=[
                                    ft.Text(f"Nombre: {nombre}", size=12, color=TEXT_DARK, weight=ft.FontWeight.W_600),
                                    ft.Text(f"Asunto: {asunto}", size=11, color=TEXT_GRAY),
                                    ft.Row(
                                        spacing=15,
                                        controls=[
                                            ft.Text(f"ID: {id_sol}", size=11, color=TEXT_GRAY),
                                            ft.Text(f"Rep. Legal: {rep_leg}", size=11, color=TEXT_GRAY),
                                            ft.Text(f"Fecha: {fecha_reg}", size=11, color=TEXT_GRAY),
                                        ]
                                    )
                                ]
                            ),
                            ft.Divider(height=1, color=DIVIDER_COLOR),
                            lista_archivos_ui
                        ]
                    )
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
                            ft.Text("Metadatos extraídos por folio", size=13, weight=ft.FontWeight.W_700, color=TEXT_DARK),
                        ]
                    ),
                    ft.Column(spacing=8, controls=cards)
                ]
            )
        )

    def _stat_col(self, title, value):
        return ft.Column(
            horizontal_alignment=ft.CrossAxisAlignment.CENTER,
            spacing=2,
            controls=[
                ft.Text(str(value), size=24, color="#FFF", weight=ft.FontWeight.W_800),
                ft.Text(title, size=11, color="#E0F7FA", weight=ft.FontWeight.W_600),
            ]
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
                            ft.Text(det, size=11, color=TEXT_GRAY),
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
                                width=4, height=20,
                                bgcolor=color,
                                border_radius=ft.BorderRadius.all(2),
                            ),
                            ft.Text(titulo, size=13, weight=ft.FontWeight.W_700, color=TEXT_DARK),
                            ft.Container(
                                padding=ft.Padding.symmetric(horizontal=8, vertical=2),
                                bgcolor=color,
                                border_radius=ft.BorderRadius.all(10),
                                content=ft.Text(str(count), size=11, color="#FFF", weight=ft.FontWeight.W_700),
                            ),
                        ],
                    ),
                    ft.Row(wrap=True, spacing=8, run_spacing=8, controls=chips)
                    if chips else ft.Text("Ninguno.", size=12, color=TEXT_MUTED, italic=True),
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
                with open(log_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                total    = data.get("total_folios", 0)
                exitosos = data.get("total_exitosos", 0)
                modo     = data.get("modo_extraccion", "—")
            except Exception:
                pass

        self._historial.insert(0, {
            "fecha":    fecha,
            "total":    total,
            "exitosos": exitosos,
            "modo":     modo,
            "rc":       rc,
        })
        self._rebuild_historial()

    def _rebuild_historial(self):
        if not self._historial:
            self.hist_column.controls = [
                ft.Container(
                    alignment=ft.Alignment.CENTER,
                    padding=ft.Padding.all(40),
                    content=ft.Column(
                        horizontal_alignment=ft.CrossAxisAlignment.CENTER,
                        spacing=8,
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
            ok  = h["rc"] == 0
            bg  = CARD_BG if i % 2 == 0 else "#F8FAFB"
            rows.append(
                ft.Container(
                    bgcolor=bg,
                    padding=ft.Padding.symmetric(horizontal=16, vertical=12),
                    border=ft.Border.only(bottom=ft.BorderSide(1, DIVIDER_COLOR)),
                    content=ft.Row(
                        vertical_alignment=ft.CrossAxisAlignment.CENTER,
                        controls=[
                            ft.Icon(
                                ft.Icons.CHECK_CIRCLE if ok else ft.Icons.CANCEL,
                                color=GREEN_OK if ok else RED_ERR,
                                size=18,
                            ),
                            ft.Container(width=10),
                            ft.Container(
                                width=130,
                                content=ft.Text(h["fecha"], size=13, color=TEXT_DARK),
                            ),
                            ft.Container(
                                width=90,
                                content=ft.Text(
                                    f"{h['exitosos']}/{h['total']} folios",
                                    size=13, color=TEAL_PRIMARY, weight=ft.FontWeight.W_600,
                                ),
                            ),
                            ft.Container(
                                expand=True,
                                content=ft.Text(
                                    f"Extracción: {h['modo']}",
                                    size=12, color=TEXT_GRAY,
                                ),
                            ),
                            ft.Container(
                                padding=ft.Padding.symmetric(horizontal=10, vertical=3),
                                border_radius=ft.BorderRadius.all(10),
                                bgcolor=GREEN_OK if ok else RED_ERR,
                                content=ft.Text(
                                    "Exitoso" if ok else "Error",
                                    size=11, color="#FFF", weight=ft.FontWeight.W_600,
                                ),
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
    #  CONSTRUCCIÓN DE PANTALLAS
    # ════════════════════════════════════════════════════

    # ── Pantalla: Procesar Folios ────────────────────────
    def _build_screen_procesar(self) -> ft.Control:

        # Tarjeta de configuración
        config_card = ft.Container(
            bgcolor=CARD_BG,
            border_radius=ft.BorderRadius.all(14),
            padding=ft.Padding.all(20),
            shadow=ft.BoxShadow(blur_radius=16, color="#10000000", offset=ft.Offset(0, 3)),
            content=ft.Column(
                spacing=14,
                controls=[
                    ft.Text("Configuración de ejecución", size=15,
                            weight=ft.FontWeight.W_700, color=TEXT_DARK),
                    ft.Divider(height=1, color=DIVIDER_COLOR),

                    # Modo de folios
                    ft.Row(
                        spacing=14,
                        vertical_alignment=ft.CrossAxisAlignment.CENTER,
                        controls=[
                            ft.Text("Modo:", size=13, color=TEXT_GRAY, width=60),
                            self.dd_modo,
                            self.txt_workers,
                            self.sw_navegador,
                        ],
                    ),
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
                            self.btn_limpiar,
                        ],
                    ),
                    self.progress_bar,
                ],
            ),
        )

        # Tarjeta de log
        log_card = ft.Container(
            height=250,
            bgcolor=CARD_BG,
            border_radius=ft.BorderRadius.all(14),
            shadow=ft.BoxShadow(blur_radius=16, color="#10000000", offset=ft.Offset(0, 3)),
            padding=ft.Padding.all(0),
            clip_behavior=ft.ClipBehavior.ANTI_ALIAS,
            content=ft.Column(
                spacing=0,
                expand=True,
                controls=[
                    # Header del log
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
                            ],
                        ),
                    ),
                    # Log terminal
                    ft.Container(
                        expand=True,
                        bgcolor=LOG_BG,
                        padding=ft.Padding.all(14),
                        content=self.log_column,
                    ),
                ],
            ),
        )

        # Resumen ejecutivo
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
            visible=True,
        )

        return ft.Column(
            spacing=16,
            expand=True,
            scroll=ft.ScrollMode.AUTO,
            controls=[
                config_card,
                log_card,
                resumen_card,
            ],
        )

    # ── Pantalla: Historial ──────────────────────────────
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
            expand=True,
            bgcolor=CARD_BG,
            border_radius=ft.BorderRadius.all(14),
            padding=ft.Padding.all(0),
            shadow=ft.BoxShadow(blur_radius=16, color="#10000000", offset=ft.Offset(0, 3)),
            clip_behavior=ft.ClipBehavior.ANTI_ALIAS,
            content=ft.Column(
                spacing=0,
                expand=True,
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

    # ── Pantalla: Configuración ──────────────────────────
    def _build_screen_config(self) -> ft.Control:

        def _field_row(label, ctrl, hint=""):
            return ft.Column(
                spacing=6,
                controls=[
                    ft.Text(label, size=12, color=TEXT_GRAY, weight=ft.FontWeight.W_600),
                    ft.Row(spacing=8, controls=[ctrl]),
                    ft.Text(hint, size=11, color=TEXT_MUTED, italic=True) if hint else ft.Container(height=0),
                ],
            )

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
                        self.txt_python_path,
                        r"Ej: python_portable\python.exe  ó  C:\Python312\python.exe",
                    ),
                    _field_row(
                        "Script principal",
                        self.txt_script_path,
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
                                        ft.Text("Configuración de credenciales", size=13,
                                                weight=ft.FontWeight.W_600, color=TEXT_DARK),
                                    ],
                                ),
                                ft.Text(
                                    "Las credenciales de SATyS (SATYS_USER / SATYS_PASS) y Azure AI "
                                    "(AZURE_DOCUMENT_INTELLIGENCE_KEY) se leen de variables de entorno o "
                                    "del archivo .env en el directorio del proyecto.",
                                    size=12, color=TEXT_GRAY,
                                ),
                            ],
                        ),
                    ),

                    ft.ElevatedButton(
                        "Guardar configuración",
                        icon=ft.Icons.SAVE_OUTLINED,
                        bgcolor=TEAL_PRIMARY,
                        color="#FFF",
                        style=ft.ButtonStyle(
                            shape=ft.RoundedRectangleBorder(radius=8),
                            elevation=0,
                        ),
                        on_click=lambda e: self.show_toast("Configuración guardada en esta sesión."),
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
                    ft.Icon(
                        icon_fill if active else icon_out,
                        size=19,
                        color=TEAL_DARK if active else TEXT_GRAY,
                    ),
                    ft.Text(
                        label, size=13.5,
                        color=TEAL_DARK if active else TEXT_GRAY,
                        weight=ft.FontWeight.W_600 if active else ft.FontWeight.W_400,
                    ),
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

        return ft.Container(
            width=230,
            bgcolor=SIDEBAR_BG,
            content=ft.Column(
                controls=[
                    # Logo
                    ft.Container(
                        padding=ft.Padding.only(left=18, right=18, top=22, bottom=10),
                        content=ft.Image(
                            src="logo.png",
                            width=150,
                            fit=ft.BoxFit.CONTAIN,
                        ),
                    ),
                    ft.Divider(height=1, color="#BDD0D1"),
                    ft.Container(height=8),
                    # Sub-título
                    ft.Container(
                        padding=ft.Padding.symmetric(horizontal=16, vertical=4),
                        content=ft.Text(
                            "AUTOMATIZACIÓN SATyS",
                            size=10,
                            color=TEXT_MUTED,
                            weight=ft.FontWeight.W_700,
                        ),
                    ),
                    ft.Container(height=4),
                    self.sidebar_nav_col,
                    ft.Container(expand=True),
                    # Pie de la barra
                    ft.Container(
                        padding=ft.Padding.only(left=16, right=16, bottom=16),
                        content=ft.Text(
                            "© CRT 2025",
                            size=11, color=TEXT_MUTED,
                        ),
                    ),
                ],
            ),
        )

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
                ft.Column(
                    spacing=2,
                    controls=[
                        ft.Text("SATyS — Control de Descarga y Procesamiento",
                                size=13, color=TEXT_GRAY),
                        ft.Text(screen_titles.get(self.active_nav, ""),
                                size=22, color=TEXT_DARK, weight=ft.FontWeight.W_700),
                    ],
                ),
                ft.Row(
                    spacing=10,
                    vertical_alignment=ft.CrossAxisAlignment.CENTER,
                    controls=[
                        ft.CircleAvatar(
                            content=ft.Icon(ft.Icons.PERSON, color="#9AA3A6"),
                            bgcolor="#E4E7E9",
                            radius=18,
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

        # Actualizar header
        self.header_row.controls[0].controls[1].value = {
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
            expand=True,
            bgcolor=PAGE_BG,
            padding=ft.Padding.only(left=28, right=28, top=22, bottom=14),
            content=ft.Column(
                spacing=18,
                expand=True,
                controls=[
                    self.header_row,
                    self.screen_container,
                    ft.Text(
                        "Optimizado para Windows  ·  © Comisión Reguladora de Telecomunicaciones 2025",
                        size=11, color=TEXT_MUTED,
                        text_align=ft.TextAlign.CENTER,
                    ),
                ],
            ),
        )

        return ft.Stack(
            expand=True,
            controls=[
                ft.Row(
                    expand=True,
                    spacing=0,
                    controls=[sidebar, main_area],
                ),
                # Toast flotante
                ft.Container(
                    top=16, right=16,
                    content=self.toast,
                ),
            ],
        )


# ════════════════════════════════════════════════════════
#  ENTRY POINT
# ════════════════════════════════════════════════════════

def main(page: ft.Page):
    page.title    = "SATyS — Gestor de Automatización CRT"
    page.bgcolor  = PAGE_BG
    page.padding  = 0

    page.window.width      = 1280
    page.window.height     = 780
    page.window.min_width  = 1000
    page.window.min_height = 620

    page.theme = ft.Theme(
        font_family="Segoe UI",
        color_scheme_seed=TEAL_PRIMARY,
    )

    app = SATySApp(page)
    page.add(app.build())


if __name__ == "__main__":
    ft.app(target=main, assets_dir="assets", view=ft.AppView.WEB_BROWSER)
