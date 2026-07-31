PARCHE SATyS — ESPERA ROBUSTA DE REGISTROS (120 SEGUNDOS)

Este parche se aplica encima de los parches anteriores de:
- una sola ejecución diaria;
- reintentos generales de extracción;
- reintentos inmediatos.

Nuevo flujo por año:
1. Selecciona y verifica el año.
2. Espera hasta 120 segundos a que aparezca al menos un Registro válido.
3. En cuanto aparece, lee todas las páginas.
4. Verifica que cada página avance realmente y no cuenta páginas repetidas.
5. Si el año falla, reabre el tablero y reintenta solo ese año, de inmediato.
6. Conserva en memoria los años ya completados.

Aplicación:

  rm -rf /tmp/satys-espera-robusta
  mkdir -p /tmp/satys-espera-robusta
  unzip -q parche-satys-espera-robusta-120s-20260722.zip \
    -d /tmp/satys-espera-robusta
  cd /tmp/satys-espera-robusta
  sudo bash aplicar_parche_espera_robusta_satys.sh

El instalador crea respaldo, valida sintaxis, ejecuta siete pruebas y revierte
los archivos si alguna validación falla. No modifica credenciales, Excel,
descargas, output, systemd ni el timer.
