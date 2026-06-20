import sys
import os
import subprocess
import threading
import queue
import re
import flet as ft

# Expresión regular para capturar el progreso (ej: [CONC] [3/19] ...)
PROGRESS_REGEX = re.compile(r"\[CONC\] \[\s*(\d+)\s*/\s*(\d+)\s*\]")

def main(page: ft.Page):
    page.title = "SATyS - Automatización de Descargas"
    page.theme_mode = ft.ThemeMode.DARK
    page.window_width = 900
    page.window_height = 800
    page.padding = 20
    page.update()

    # Variables de estado
    archivo_folios_path = None
    is_running = False
    process = None

    # UI Components - Sección 1: Entradas
    txt_folios = ft.TextField(
        label="Ingresar Folios Manualmente (separados por espacio)",
        expand=True,
        multiline=False
    )
    
    txt_archivo = ft.Text("Ningún archivo seleccionado", italic=True, color=ft.Colors.GREY_400)
    
    async def on_click_pick(e):
        nonlocal archivo_folios_path
        files = await file_picker.pick_files(allow_multiple=False, allowed_extensions=["txt"])
        if files and len(files) > 0:
            archivo_folios_path = files[0].path
            txt_archivo.value = f"Archivo: {os.path.basename(archivo_folios_path)}"
        else:
            archivo_folios_path = None
            txt_archivo.value = "Ningún archivo seleccionado"
        page.update()

    file_picker = ft.FilePicker()

    btn_file = ft.Button(
        content="Seleccionar archivo .txt",
        icon=ft.Icons.UPLOAD_FILE,
        on_click=on_click_pick
    )

    # UI Components - Sección 2: Configuración
    switch_headless = ft.Switch(label="Modo Invisible (Headless)", value=True)
    
    txt_limite_val = ft.Text("0 (Sin límite)")
    def on_limite_change(e):
        val = int(e.control.value)
        txt_limite_val.value = f"{val}" if val > 0 else "0 (Sin límite)"
        page.update()
        
    slider_limite = ft.Slider(min=0, max=100, divisions=100, value=0, label="{value}", on_change=on_limite_change)

    txt_workers_val = ft.Text("3")
    def on_workers_change(e):
        txt_workers_val.value = str(int(e.control.value))
        page.update()

    slider_workers = ft.Slider(min=1, max=100, divisions=99, value=3, label="{value}", on_change=on_workers_change)

    # UI Components - Sección 3: Consola y Progreso
    progress_bar = ft.ProgressBar(value=0, color="amber", bgcolor="#eeeeee", visible=False)
    txt_progreso = ft.Text("Progreso: 0%", visible=False)
    
    console_output = ft.ListView(
        expand=True,
        spacing=2,
        auto_scroll=True,
    )
    
    console_container = ft.Container(
        content=console_output,
        bgcolor=ft.Colors.BLACK,
        border_radius=5,
        padding=10,
        expand=True
    )

    def write_console(text: str, color=ft.Colors.WHITE):
        console_output.controls.append(ft.Text(text, color=color, size=12, font_family="Consolas"))
        if len(console_output.controls) > 500:
            console_output.controls.pop(0)
        page.update()

    # Lógica de ejecución
    def read_output(pipe, q):
        try:
            for line in iter(pipe.readline, ''):
                q.put(line)
        finally:
            pipe.close()

    def procesar_cola(q):
        nonlocal is_running
        while is_running or not q.empty():
            try:
                line = q.get_nowait()
                sys.stdout.write(line)
                sys.stdout.flush()
                line_str = line.strip()
                
                # Color coding básico
                c = ft.Colors.WHITE
                if "ERROR" in line_str or "CRITICAL" in line_str or "[ERR]" in line_str or "[XX]" in line_str:
                    c = ft.Colors.RED_400
                elif "WARN" in line_str:
                    c = ft.Colors.ORANGE_400
                elif "OK" in line_str or "EXITOSO" in line_str:
                    c = ft.Colors.GREEN_400
                elif "INFO" in line_str:
                    c = ft.Colors.BLUE_200
                
                write_console(line_str, color=c)
                
                # Extraer progreso
                match = PROGRESS_REGEX.search(line_str)
                if match:
                    actual = int(match.group(1))
                    total = int(match.group(2))
                    if total > 0:
                        progress_bar.value = actual / total
                        txt_progreso.value = f"Progreso: {int((actual/total)*100)}% ({actual}/{total})"
                        page.update()

            except queue.Empty:
                if process and process.poll() is not None and q.empty():
                    break
                pass
            import time
            time.sleep(0.01)

    def run_script(e):
        nonlocal is_running, process
        if is_running:
            # Botón funciona como cancelar
            if process:
                process.terminate()
            write_console("\n[!] PROCESO CANCELADO POR EL USUARIO", ft.Colors.RED)
            reset_ui()
            return

        folios_str = txt_folios.value.strip()
        if not folios_str and not archivo_folios_path:
            write_console("Error: Debes ingresar folios manuales o seleccionar un archivo.", ft.Colors.RED)
            return

        # Bloquear UI
        is_running = True
        btn_run.content = "🛑 DETENER DESCARGA"
        btn_run.bgcolor = ft.Colors.RED_700
        progress_bar.value = 0
        progress_bar.visible = True
        txt_progreso.visible = True
        console_output.controls.clear()
        write_console(">>> Iniciando programa...", ft.Colors.CYAN)
        page.update()

        # Construir argumentos
        script_path = os.path.join(os.path.dirname(__file__), "Parte1_descarga.py")
        cmd = [sys.executable, "-u", script_path]

        if folios_str:
            cmd.extend(["--folios"] + folios_str.split())
        if archivo_folios_path:
            cmd.extend(["--archivo", archivo_folios_path])
        
        if switch_headless.value:
            cmd.append("--headless")
        else:
            cmd.append("--visible")
            
        cmd.extend(["--limite", str(int(slider_limite.value))])
        cmd.extend(["--workers", str(int(slider_workers.value))])

        # Hilo para correr el proceso
        def thread_run():
            nonlocal is_running, process
            try:
                # OJO: stdout y stderr redirigidos
                process = subprocess.Popen(
                    cmd, 
                    stdout=subprocess.PIPE, 
                    stderr=subprocess.STDOUT, 
                    text=True, 
                    encoding="utf-8",
                    errors="replace"
                )
                
                q = queue.Queue()
                t_reader = threading.Thread(target=read_output, args=(process.stdout, q))
                t_reader.start()
                
                # Consumir la cola en este hilo y actualizar Flet
                procesar_cola(q)
                
                process.wait()
                t_reader.join()
                
                if process.returncode == 0:
                    write_console("\n>>> EJECUCIÓN COMPLETADA CON ÉXITO", ft.Colors.GREEN)
                    progress_bar.value = 1.0
                    txt_progreso.value = "Progreso: 100%"
                elif process.returncode != 0 and is_running:
                    write_console(f"\n>>> EJECUCIÓN FINALIZADA CON CÓDIGO DE ERROR {process.returncode}", ft.Colors.RED)

            except Exception as ex:
                write_console(f"\n>>> ERROR CRÍTICO AL INICIAR: {ex}", ft.Colors.RED)
            finally:
                reset_ui()

        threading.Thread(target=thread_run, daemon=True).start()

    def reset_ui():
        nonlocal is_running
        is_running = False
        btn_run.content = "🚀 INICIAR DESCARGA"
        btn_run.bgcolor = ft.Colors.BLUE_700
        page.update()

    btn_run = ft.Button(
        content="🚀 INICIAR DESCARGA",
        bgcolor=ft.Colors.BLUE_700,
        color=ft.Colors.WHITE,
        height=50,
        on_click=run_script,
        style=ft.ButtonStyle(shape=ft.RoundedRectangleBorder(radius=8))
    )

    # Layout Principal
    page.add(
        ft.Text("SATyS - Interfaz de Descargas", size=28, weight=ft.FontWeight.BOLD, color=ft.Colors.BLUE_300),
        ft.Divider(),
        
        ft.Text("1. Selección de Folios", size=18, weight=ft.FontWeight.BOLD),
        ft.Row([txt_folios], alignment=ft.MainAxisAlignment.START),
        ft.Row([btn_file, txt_archivo], alignment=ft.MainAxisAlignment.START),
        
        ft.Container(height=10),
        ft.Text("2. Configuración de Ejecución", size=18, weight=ft.FontWeight.BOLD),
        switch_headless,
        ft.Row([
            ft.Column([
                ft.Row([ft.Text("Límite de folios:"), txt_limite_val]),
                slider_limite
            ], expand=1),
            ft.Column([
                ft.Row([ft.Text("Trabajadores (Ventanas paralelas):"), txt_workers_val]),
                slider_workers
            ], expand=1),
        ]),
        
        ft.Container(height=10),
        ft.Row([btn_run], alignment=ft.MainAxisAlignment.CENTER),
        
        ft.Container(height=10),
        ft.Row([txt_progreso, progress_bar], alignment=ft.MainAxisAlignment.SPACE_BETWEEN),
        console_container
    )

if __name__ == "__main__":
    ft.run(main)
