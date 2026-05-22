import easyocr

def extraer_texto(ruta_imagen):
    # Inicializamos el lector en español ('es') 
    # También puedes añadir inglés ('en') si el texto es bilingüe
    reader = easyocr.Reader(['es'])

    # Leemos la imagen
    # detail=0 devuelve solo el texto; detail=1 devuelve coordenadas y confianza
    resultados = reader.readtext(ruta_imagen, detail=0)

    # Imprimimos el resultado en la terminal
    print("\n--- Texto Extraído ---")
    for linea in resultados:
        print(linea)
    print("----------------------\n")

if __name__ == "__main__":
    # Cambia esto por el nombre de tu imagen
    mi_imagen = 'prueba.png' 
    
    try:
        extraer_texto(mi_imagen)
    except Exception as e:
        print(f"Ocurrió un error: {e}")