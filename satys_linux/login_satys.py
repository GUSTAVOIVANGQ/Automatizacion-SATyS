import requests
import urllib3

# Desactivar advertencias de certificados SSL
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

def hacer_login():
    url_login = "https://satys.ift.org.mx/VerifyLogin"
    
    # Credenciales proporcionadas
    credenciales = {
        'username': 'david.palestina@ift.org.mx',
        'password': 'Crt20261234**'
    }
    
    print(f"📡 Enviando credenciales a: {url_login}")
    
    # Usar una sesión para mantener las cookies automáticamente
    sesion = requests.Session()
    
    # Simular ser un navegador
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    }
    
    try:
        # Enviar petición POST. allow_redirects=True permite seguir la redirección si el login es exitoso
        respuesta = sesion.post(
            url_login, 
            data=credenciales, 
            headers=headers,
            verify=False, 
            allow_redirects=True
        )
        
        print("\n" + "="*50)
        print("RESULTADO DEL INICIO DE SESIÓN")
        print("="*50)
        print(f"Código de estado HTTP: {respuesta.status_code}")
        print(f"URL final después del POST: {respuesta.url}")
        
        # Lógica para detectar si entró:
        # Normalmente, si falla se queda en VerifyLogin o index.html, si entra se va a un panel o dashboard
        if "VerifyLogin" not in respuesta.url and "index.html" not in respuesta.url:
            print("\n✅ ¡LOGIN EXITOSO!")
            print(f"El servidor te redirigió a: {respuesta.url}")
        else:
            print("\n⚠️ ALERTA: Es posible que el login haya fallado o las credenciales sean incorrectas.")
            print("Seguimos en la página de inicio o verificación.")
            
        print("\n--- Cookies establecidas por el servidor ---")
        if sesion.cookies:
            for cookie in sesion.cookies:
                print(f"🍪 {cookie.name}: {cookie.value}")
        else:
            print("No se establecieron cookies.")
            
    except Exception as e:
        print(f"\n❌ Error al intentar iniciar sesión: {e}")

if __name__ == "__main__":
    hacer_login()
