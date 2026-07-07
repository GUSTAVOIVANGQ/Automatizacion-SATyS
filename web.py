import flet as ft

def main(page: ft.Page):
    page.title = "Navegador Integrado con Flet"
    
    # Crear el control WebView con la URL deseada
    visor_web = ft.WebView(
        url="https://flet.dev",
        expand=True  # Permite que el WebView ocupe todo el espacio disponible
    )
    
    # Agregar el control a la página
    page.add(visor_web)

ft.app(target=main)