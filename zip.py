import win32com.client as win32

# 1. Abre Outlook
outlook = win32.Dispatch('outlook.application')

# 2. Crea un correo nuevo
mail = outlook.CreateItem(0)

# 3. Define destinatario, asunto y cuerpo
mail.To = 'gustavo.garcia@crt.gob.mx'
mail.Subject = 'Correo de prueba desde Python'
mail.Body = 'Hola, este es un mensaje automático.'

# (Opcional) Adjunta un archivo
# mail.Attachments.Add('C:\\Ruta\\A\\Tu\\Archivo.pdf')

# 4. Envía el correo
mail.Send()
print("Correo enviado")
