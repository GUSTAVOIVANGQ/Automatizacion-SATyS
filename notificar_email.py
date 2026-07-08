#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
notificar_email.py
==================
Modulo de notificacion por correo electronico para el sistema de
automatizacion SATyS - Direccion Ejecutiva de Indicadores (DEI), CRT.

Envia un resumen HTML al finalizar el proceso de descarga y extraccion
de datos por numeros de registro en el portal SATyS.

Uso independiente (para pruebas):
  .\python-3.11.9-embed-amd64\python.exe notificar_email.py --test

Dependencias: solo libreria estandar de Python (smtplib, email).
"""

from __future__ import annotations

import smtplib
import json
import os
from datetime import datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path
from typing import Optional

# ─────────────────────────────────────────────────────────────────────
#  CONFIGURACION DE CORREO
# ─────────────────────────────────────────────────────────────────────

GMAIL_REMITENTE    = "naviia8989@gmail.com"
GMAIL_APP_PASSWORD = "swxmqcirufgtmlvh"   # App password Gmail (sin espacios)

DESTINATARIOS = [
    "gustavo.garcia@crt.gob.mx",
    "david.palestina@crt.org.mx",
    "brandon.gonzalez@crt.gob.mx",
    "antonio.sandoval@crt.gob.mx",
]

AUTOR          = "David Palestina Ramirez y equipo"
ORGANIZACION   = "Direccion Ejecutiva de Indicadores (DEI)"
CONSEJO        = "Honorable Consejo de Expertos de la CRT"
CARPETA_SALIDA = r"Z:\DEI_DATOS\SATyS"

# ─────────────────────────────────────────────────────────────────────
#  TABLA DE REGISTROS
# ─────────────────────────────────────────────────────────────────────

def _filas_html(registros, max_mostrar=300):
    if not registros:
        return "<tr><td colspan='3' style='text-align:center;color:#6b7280;'>Sin datos</td></tr>"

    filas = []
    total = len(registros)
    for r in registros[:max_mostrar]:
        folio = r.get("folio") or r.get("registro") or "?"
        ok = r.get("rpc_ok") and r.get("organizado_ok") and r.get("excel_ok")
        nombre = r.get("nombre_operador") or (r.get("rpc_resultado") or {}).get("nombre_completo") or ""

        if ok:
            emoji, estado, bg, tc = "OK", "Exitoso",           "#f0fdf4", "#166534"
        elif nombre:
            emoji, estado, bg, tc = ">>", "Revision manual",   "#fefce8", "#854d0e"
        else:
            emoji, estado, bg, tc = "XX", "Error",             "#fef2f2", "#991b1b"

        filas.append(
            f"<tr style='background:{bg};'>"
            f"<td style='padding:5px 10px;font-family:monospace;font-size:13px;'>{folio}</td>"
            f"<td style='padding:5px 10px;color:{tc};font-weight:600;'>[{emoji}] {estado}</td>"
            f"<td style='padding:5px 10px;font-size:13px;color:#374151;'>{nombre or '-'}</td>"
            f"</tr>"
        )

    html = "\n".join(filas)
    if total > max_mostrar:
        html += (
            f"<tr><td colspan='3' style='text-align:center;color:#6b7280;"
            f"padding:8px;font-style:italic;'>"
            f"... y {total - max_mostrar} registros mas (ver archivo de log)</td></tr>"
        )
    return html


# ─────────────────────────────────────────────────────────────────────
#  CUERPO HTML DEL CORREO
# ─────────────────────────────────────────────────────────────────────

def construir_html(fecha_ejecucion, total_registros, exitosos, sin_operador,
                   errores, registros, carpeta_salida=CARPETA_SALIDA,
                   autor=AUTOR, organizacion=ORGANIZACION, consejo=CONSEJO):

    ahora = datetime.now().strftime("%d/%m/%Y %H:%M:%S")
    try:
        dt = datetime.fromisoformat(fecha_ejecucion)
        fecha_fmt = dt.strftime("%d/%m/%Y %H:%M:%S")
    except Exception:
        fecha_fmt = str(fecha_ejecucion)

    filas = _filas_html(registros)

    pct_ok  = round(exitosos     / total_registros * 100) if total_registros else 0
    pct_rev = round(sin_operador / total_registros * 100) if total_registros else 0
    pct_err = round(errores      / total_registros * 100) if total_registros else 0

    return f"""<!DOCTYPE html>
<html lang="es">
<head><meta charset="UTF-8">
<title>Reporte SATyS DEI</title></head>
<body style="margin:0;padding:0;background:#f3f4f6;font-family:'Segoe UI',Arial,sans-serif;">
<table width="100%" cellpadding="0" cellspacing="0" style="background:#f3f4f6;padding:30px 0;">
<tr><td align="center">
<table width="680" cellpadding="0" cellspacing="0"
       style="background:#fff;border-radius:12px;overflow:hidden;
              box-shadow:0 4px 24px rgba(0,0,0,.10);max-width:680px;">

<!-- CABECERA -->
<tr><td style="background:linear-gradient(135deg,#1d4ed8 0%,#0f172a 100%);
               padding:36px 40px;text-align:center;">
  <p style="margin:0 0 6px;color:#93c5fd;font-size:13px;letter-spacing:2px;
            text-transform:uppercase;">Sistema de Automatizacion</p>
  <h1 style="margin:0;color:#fff;font-size:26px;font-weight:700;">
    Proceso SATyS Completado</h1>
  <p style="margin:10px 0 0;color:#bfdbfe;font-size:14px;">
    Descarga y extraccion de datos por numero de registro</p>
</td></tr>

<!-- FECHA/AUTOR -->
<tr><td style="background:#1e3a5f;padding:14px 40px;text-align:center;">
  <p style="margin:0;color:#cbd5e1;font-size:13px;">
    Ejecucion: <strong style="color:#e2e8f0;">{fecha_fmt}</strong>
    &nbsp;|&nbsp;
    Autor: <strong style="color:#e2e8f0;">{autor}</strong></p>
  <p style="margin:4px 0 0;color:#94a3b8;font-size:12px;">
    {organizacion} &middot; {consejo}</p>
</td></tr>

<!-- TARJETAS -->
<tr><td style="padding:30px 40px 20px;">
  <h2 style="margin:0 0 18px;color:#1e293b;font-size:17px;
             border-left:4px solid #2563eb;padding-left:12px;">
    Resumen Ejecutivo</h2>
  <table width="100%" cellpadding="0" cellspacing="8">
    <tr>
      <td width="25%"><div style="background:#eff6ff;border:1px solid #bfdbfe;
        border-radius:10px;padding:18px 14px;text-align:center;">
        <div style="font-size:30px;font-weight:800;color:#1d4ed8;">{total_registros:,}</div>
        <div style="font-size:11px;color:#3b82f6;font-weight:600;margin-top:4px;">TOTAL PROCESADOS</div>
      </div></td>
      <td width="25%"><div style="background:#f0fdf4;border:1px solid #bbf7d0;
        border-radius:10px;padding:18px 14px;text-align:center;">
        <div style="font-size:30px;font-weight:800;color:#16a34a;">{exitosos:,}</div>
        <div style="font-size:11px;color:#22c55e;font-weight:600;margin-top:4px;">EXITOSOS</div>
      </div></td>
      <td width="25%"><div style="background:#fefce8;border:1px solid #fef08a;
        border-radius:10px;padding:18px 14px;text-align:center;">
        <div style="font-size:30px;font-weight:800;color:#d97706;">{sin_operador:,}</div>
        <div style="font-size:11px;color:#f59e0b;font-weight:600;margin-top:4px;">REVISION MANUAL</div>
      </div></td>
      <td width="25%"><div style="background:#fef2f2;border:1px solid #fecaca;
        border-radius:10px;padding:18px 14px;text-align:center;">
        <div style="font-size:30px;font-weight:800;color:#dc2626;">{errores:,}</div>
        <div style="font-size:11px;color:#ef4444;font-weight:600;margin-top:4px;">ERRORES</div>
      </div></td>
    </tr>
  </table>

  <!-- BARRA DE PROGRESO -->
  <div style="margin-top:20px;background:#f1f5f9;border-radius:8px;height:12px;overflow:hidden;">
    <div style="display:inline-block;width:{pct_ok}%;background:#22c55e;height:12px;vertical-align:top;"></div><div
         style="display:inline-block;width:{pct_rev}%;background:#f59e0b;height:12px;vertical-align:top;"></div><div
         style="display:inline-block;width:{pct_err}%;background:#ef4444;height:12px;vertical-align:top;"></div>
  </div>
  <div style="font-size:11px;color:#6b7280;margin-top:5px;text-align:right;">
    {pct_ok}% exitosos &middot; {pct_rev}% revision &middot; {pct_err}% errores
  </div>
</td></tr>

<!-- CARPETA DE SALIDA -->
<tr><td style="padding:0 40px 24px;">
  <div style="background:#f8fafc;border:1px solid #e2e8f0;border-radius:8px;padding:16px 20px;">
    <p style="margin:0 0 6px;font-size:12px;color:#64748b;font-weight:600;
              text-transform:uppercase;letter-spacing:1px;">
      Ubicacion del Archivo de Salida</p>
    <p style="margin:0;font-family:'Courier New',monospace;font-size:15px;
              color:#1d4ed8;font-weight:700;">{carpeta_salida}</p>
    <p style="margin:6px 0 0;font-size:12px;color:#94a3b8;">
      output/ y TramitesCRT.xlsx sincronizados en la carpeta de red compartida.</p>
  </div>
</td></tr>

<!-- TABLA DE REGISTROS -->
<tr><td style="padding:0 40px 30px;">
  <h2 style="margin:0 0 14px;color:#1e293b;font-size:17px;
             border-left:4px solid #2563eb;padding-left:12px;">
    Lista de Registros Procesados ({total_registros:,})</h2>
  <table width="100%" cellpadding="0" cellspacing="0"
         style="border-collapse:collapse;border:1px solid #e2e8f0;
                border-radius:8px;overflow:hidden;font-size:13px;">
    <thead>
      <tr style="background:#1e293b;color:#f1f5f9;">
        <th style="padding:10px 14px;text-align:left;font-size:12px;font-weight:600;">
          NUMERO DE REGISTRO</th>
        <th style="padding:10px 14px;text-align:left;font-size:12px;font-weight:600;">
          ESTADO</th>
        <th style="padding:10px 14px;text-align:left;font-size:12px;font-weight:600;">
          OPERADOR / CONCESIONARIA</th>
      </tr>
    </thead>
    <tbody>
      {filas}
    </tbody>
  </table>
</td></tr>

<!-- PIE -->
<tr><td style="background:#0f172a;padding:24px 40px;text-align:center;">
  <p style="margin:0 0 4px;color:#94a3b8;font-size:13px;">
    Correo generado automaticamente por el Sistema de Automatizacion SATyS.</p>
  <p style="margin:0;color:#64748b;font-size:12px;">
    {autor} &middot; {organizacion} &middot; {consejo}</p>
  <p style="margin:6px 0 0;color:#475569;font-size:11px;">Generado el {ahora}</p>
</td></tr>

</table></td></tr></table>
</body></html>"""


# ─────────────────────────────────────────────────────────────────────
#  ENVIO POR GMAIL
# ─────────────────────────────────────────────────────────────────────

def enviar_notificacion(
    total_registros: int,
    exitosos: int,
    sin_operador: int,
    errores: int,
    registros: list,
    fecha_ejecucion=None,
    carpeta_salida: str = CARPETA_SALIDA,
    destinatarios=None,
    remitente: str = GMAIL_REMITENTE,
    app_password: str = GMAIL_APP_PASSWORD,
) -> bool:
    if destinatarios is None:
        destinatarios = DESTINATARIOS
    if fecha_ejecucion is None:
        fecha_ejecucion = datetime.now().isoformat()

    ahora_str = datetime.now().strftime("%d/%m/%Y %H:%M")
    if errores == 0 and sin_operador == 0:
        estado = "Completado sin errores"
    elif errores > 0:
        estado = f"Completado con {errores} error(es)"
    else:
        estado = f"Completado - {sin_operador} en revision manual"

    asunto = f"[SATyS-DEI] {estado} - {total_registros:,} registros - {ahora_str}"

    html_body = construir_html(
        fecha_ejecucion=fecha_ejecucion,
        total_registros=total_registros,
        exitosos=exitosos,
        sin_operador=sin_operador,
        errores=errores,
        registros=registros,
        carpeta_salida=carpeta_salida,
    )

    texto_plano = (
        f"Proceso SATyS finalizado - {ahora_str}\n"
        f"{'='*60}\n"
        f"Total procesados : {total_registros:,}\n"
        f"Exitosos         : {exitosos:,}\n"
        f"Revision manual  : {sin_operador:,}\n"
        f"Errores          : {errores:,}\n\n"
        f"Ubicacion de salida: {carpeta_salida}\n\n"
        f"Autor: {AUTOR}\n"
        f"{ORGANIZACION} - {CONSEJO}\n"
    )

    msg = MIMEMultipart("alternative")
    msg["Subject"] = asunto
    msg["From"]    = f"Sistema SATyS DEI <{remitente}>"
    msg["To"]      = ", ".join(destinatarios)
    msg.attach(MIMEText(texto_plano, "plain", "utf-8"))
    msg.attach(MIMEText(html_body,   "html",  "utf-8"))

    try:
        print(f"\n{'='*60}")
        print(f"  NOTIFICACION POR CORREO ELECTRONICO")
        print(f"{'='*60}")
        print(f"  Enviando a {len(destinatarios)} destinatario(s)...")
        password_limpia = app_password.replace(" ", "")
        with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=30) as smtp:
            smtp.login(remitente, password_limpia)
            smtp.sendmail(remitente, destinatarios, msg.as_bytes())
        print(f"  Correo enviado exitosamente.")
        print(f"  Destinatarios: {', '.join(destinatarios)}")
        print(f"{'='*60}\n")
        return True
    except smtplib.SMTPAuthenticationError as e:
        print(f"  ERROR de autenticacion Gmail: {e}")
        return False
    except Exception as e:
        print(f"  ERROR al enviar correo: {e}")
        return False


def enviar_desde_log_json(log_json_path, destinatarios=None) -> bool:
    """Lee procesamiento_log_registros.json y envia el correo."""
    log_json_path = Path(log_json_path)
    if not log_json_path.exists():
        print(f"  Advertencia: no se encontro el log JSON: {log_json_path}")
        return enviar_notificacion(0, 0, 0, 0, [], destinatarios=destinatarios)

    try:
        with open(log_json_path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception as e:
        print(f"  Error al leer {log_json_path}: {e}")
        return False

    return enviar_notificacion(
        total_registros = data.get("total_registros", 0),
        exitosos        = data.get("total_exitosos", 0),
        sin_operador    = data.get("total_sin_operador", 0),
        errores         = data.get("total_errores", 0),
        registros       = data.get("resultados", []),
        fecha_ejecucion = data.get("fecha_ejecucion"),
        destinatarios   = destinatarios,
    )


# ─────────────────────────────────────────────────────────────────────
#  MODO PRUEBA
# ─────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description="Notificacion correo SATyS")
    ap.add_argument("--test", action="store_true", help="Enviar correo de prueba")
    ap.add_argument("--log",  default="",          help="Ruta al JSON de log")
    args = ap.parse_args()

    if args.log:
        ok = enviar_desde_log_json(args.log)
    else:
        print("Enviando correo de prueba con datos ficticios...")
        ok = enviar_notificacion(
            total_registros=4,
            exitosos=2,
            sin_operador=1,
            errores=1,
            registros=[
                {"folio": "CRT26-002483", "rpc_ok": True,  "organizado_ok": True,  "excel_ok": True,
                 "nombre_operador": "Telmex S.A. de C.V.",
                 "rpc_resultado": {"nombre_completo": "Telmex S.A. de C.V."}},
                {"folio": "CRT26-002490", "rpc_ok": True,  "organizado_ok": True,  "excel_ok": True,
                 "nombre_operador": "IENTC, S. DE R.L. DE C.V.",
                 "rpc_resultado": {"nombre_completo": "IENTC, S. DE R.L. DE C.V."}},
                {"folio": "CRT26-002501", "rpc_ok": False, "organizado_ok": True,  "excel_ok": False,
                 "nombre_operador": "Operador Desconocido SA"},
                {"folio": "CRT26-002512", "rpc_ok": False, "organizado_ok": False, "excel_ok": False,
                 "nombre_operador": None},
            ],
            fecha_ejecucion=datetime.now().isoformat(),
        )
    raise SystemExit(0 if ok else 1)
