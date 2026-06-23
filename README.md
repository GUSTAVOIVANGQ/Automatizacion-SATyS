# 📋 Proyecto SATyS - Automatización de Descargas y Procesamiento

**Sistema Automatizado de Trámites y Servicios (SATyS)**
**Comisión Reguladora de Telecomunicaciones (IFT)**

---

## 🖥️ Interfaz Gráfica (¡NUEVO!)

Se ha implementado una nueva **Interfaz Gráfica de Usuario (GUI)** amigable e intuitiva para orquestar todo el flujo de trabajo sin necesidad de usar comandos en la terminal. Desde aquí podrás seguir el progreso en tiempo real y ver un Resumen Ejecutivo interactivo de los resultados.

### 🚀 Mejor Configuración Recomendada (Ideal)

Para asegurar el mejor rendimiento, velocidad y estabilidad, recomendamos ajustar la interfaz de la siguiente forma:

- **Modo:** Utiliza la opción para cargar un **Archivo TXT con folios** (ej. `folios.txt`).
- **Ventanas Playwright:** Configurar en **10** (permite paralelizar múltiples descargas de manera rápida sin llegar a saturar la red o alentar el navegador).
- **Mostrar navegador:** Mantener el interruptor **Apagado** (modo "Headless"). Esto evita abrir docenas de ventanas visibles de Chromium, consumiendo muchísima menos memoria y acelerando el tiempo total.

Para iniciar la interfaz, solo necesitas hacer doble clic o ejecutar:

```bash
.\python-3.11.9-embed-amd64\python.exe ui_satys.py
```

### 📸 Galería de la Interfaz

A continuación se muestra el aspecto y flujo de la aplicación:

![Pantalla Principal](Screenshots/A1.png)
![Configuración y Log](Screenshots/A2.png)
![Procesamiento](Screenshots/A3.png)
![Resumen Ejecutivo](Screenshots/A4.png)
![Historial](Screenshots/A5.png)

---

## 🎯 Descripción General

Automatización completa del flujo de trabajo para la descarga, procesamiento y organización de archivos del sistema SATyS del Instituto Federal de Telecomunicaciones (IFT). El sistema extrae información de documentos PDF, consulta el Registro Público de Concesiones (RPC) y actualiza automáticamente una hoja de cálculo de control.

### 🔄 Flujo Completo del Proceso (Actualizado)

*El proceso se ejecuta de inicio a fin de forma consolidada, activando todas las partes del sistema, incluyendo la extracción de datos inteligente y validaciones del PDF.*

```
┌─────────────────────────────────────────────────────────────┐
│                    PROYECTO SATyS                            │
├─────────────────────────────────────────────────────────────┤
│                                                              │
│  PARTE 1 — DESCARGA AUTOMÁTICA                              │
│  ├── Login en https://satys.ift.org.mx/                     │
│  ├── Búsqueda de folios en listados de Oficialía            │
│  ├── Descarga en paralelo de todos los archivos asociados   │
│  └── Organización temporal en /descargas/<folio>/           │
│                                                             │
│  PARTE 2 — EXTRACCIÓN DE DATOS PDF (Deshabilitado)          │
│  ├── Localización del PDF principal en la carpeta           │
│  ├── Lectura y extracción estructurada de JSON Metadata     │
│  └── Identificación precisa del Nombre o Razón Social       │
│                                                             │
│  PARTE 3 — BÚSQUEDA EN RPC Y DESCARGA DE BD                 │
│  ├── Verificación y descarga de la última BD de Concesiones │
│  ├── Búsqueda inteligente por nombre de operador en Excel   │
│  ├── Extracción de Folio Electrónico y Número RPC           │
│  └── Construcción de ruta estandarizada                     │
│                                                             │
│  PARTE 4 — ACTUALIZACIÓN DE EXCEL Y CARPETAS                │
│  ├── Localización de fila por folio en Excel de control     │
│  ├── Inserción de resultados y marcado de formatos descarg. │
│  └── Traslado final ordenado a carpetas limpias en /output/ │
│                                                              │
└─────────────────────────────────────────────────────────────┘
```

---

## ✅ Validaciones del Sistema

El programa incorpora múltiples niveles de validación para garantizar la integridad de los datos de principio a fin:

1. **Validación de Identidad del Operador:** Cruza el nombre o ID obtenido en el SATyS contra el padrón oficial actualizado del RPC. Si la precisión (`score`) no es perfecta o hay múltiples empates, se aísla el caso para **revisión manual**, evitando falsos positivos.
2. **Validación de Integridad PDF/JSON:** Antes de extraer datos o copiar, se certifica que el archivo no esté corrupto y se valida que provenga del trámite esperado.
3. **Control Automático de Base de Datos:** Cada vez que el programa inicia, consulta para saber si existe una versión más nueva del Excel de Concesiones RPC, actualizándola en segundo plano si es necesario para tener los datos de dictaminación más recientes.
4. **Resiliencia de Conexión (Timeouts/Retries):** Si la web del SATyS demora en cargar, falla una petición o expira la sesión, el script tiene capacidad de reiniciar la descarga de ese folio y reintentar.

---

## 📁 Estructura del Proyecto

```
proyecto_satys/
│
├── ui_satys.py                 # 🟢 Interfaz Gráfica Principal (¡NUEVO!)
├── main_procesar.py            # Orquestador lógico (Ejecuta P1, P3 y P4)
├── Parte1_descarga.py          # Automatización web de SATyS (Playwright)
├── Parte3_rpc.py               # Motor de búsqueda y homologación en RPC
├── Parte4_excel.py             # Escritura final en Excel y carpetas /output/
├── README.md                   # Documentación actual
│
├── descargas/                  # Carpeta de tránsito (archivos recién descargados)
├── output/                     # 📁 Destino Final: carpetas homologadas y limpias
│   ├── 518998_telecomunicación.../
│   └── output_sin_operador/    # Para folios de revisión manual
│
└── TrámitesCRT.xlsx            # Hoja de cálculo de control maestro
```

---

## 📦 Instalación

*(Si requieres usarlo fuera del empaquetado portable de Python embed)*

1. **Dependencias:**

```bash
pip install playwright pdfplumber fuzzywuzzy python-Levenstein openpyxl flet
playwright install chromium
```

---

## 🚀 Uso en Terminal (Modo Avanzado)

Aunque ahora tenemos la Interfaz (GUI), si necesitas ejecutar todo desde línea de comandos (Powershell/CMD), las instrucciones consolidadas son:

```bash
# Ejecutar TODO el proceso con folios específicos (por argumentos):
.\python-3.11.9-embed-amd64\python.exe main_procesar.py 164045 164046

# Ejecutar proceso en segundo plano (Headless) leyendo folios de un TXT y usando 10 ventanas:
.\python-3.11.9-embed-amd64\python.exe main_procesar.py --archivo-folios folios.txt --headless --workers 10

# Modo Exploración Automática: Buscar y descargar los siguientes 27 folios a partir del 6407:
.\python-3.11.9-embed-amd64\python.exe main_procesar.py --buscar 27 --desde 6407

# Solo extraer y procesar archivos ya descargados (sin entrar a la web SATyS):
.\python-3.11.9-embed-amd64\python.exe main_procesar.py --solo-procesar

# Forzar la reconstrucción total de la base de datos local de Concesiones (RPC):
.\python-3.11.9-embed-amd64\python.exe main_procesar.py --rebuild-catalogo
```

### 📋 Lista completa de argumentos disponibles

| Argumento             | Descripción |
| --------------------- | ----------- |
| `[folios]`            | (Opcional) Números de folios separados por espacio para procesar directamente. Ej: `main_procesar.py 6801 6802`. |
| `--archivo-folios`    | Ruta a un archivo `.txt` que contiene un folio por línea. Ej: `--archivo-folios folios.txt`. |
| `--headless`          | Ejecuta Playwright en segundo plano ocultando la ventana del navegador (recomendado para velocidad). |
| `--workers N`         | Define el número máximo de ventanas/pestañas simultáneas. Por defecto es `10`. |
| `--solo-procesar`     | Omite la Parte 1 (automatización web de descarga) y procede directamente a extraer texto y buscar en el RPC usando los PDFs ya guardados localmente. |
| `--buscar N`          | Automatiza la búsqueda secuencial hacia adelante de `N` folios. |
| `--desde X`           | Establece el folio base (ej. `6407`) para empezar a sumar secuencialmente si se utiliza `--buscar`. |
| `--no-organizar`      | Extrae los datos y actualiza el Excel, pero omite el movimiento final de los archivos a la carpeta limpia `output/`. |
| `--rebuild-catalogo`  | Ignora el archivo caché local de operadores y reconstruye el catálogo RPC desde cero descargándolo de nuevo de la base oficial. |

---

## 📊 Columnas del Excel Actualizadas

| Columna                | Letra | Contenido                       | Ejemplo                                                                   |
| ---------------------- | ----- | ------------------------------- | ------------------------------------------------------------------------- |
| Solicitante Promovente | F     | Nombre del operador             | TELECOMUNICACIÓN Y MERCADOTECNIA DE MONTERREY, S.A. DE C.V.              |
| Ruta                   | N     | Ruta construida desde RPC       | 518998_telecomunicación_y_mercadotecnia_de_monterrey_s_a_de_c_v\01 EN\VE |
| R001-R027              | O-AQ  | "1" si el formato fue detectado | 1                                                                         |
| NOTAS_VICTOR           | AP    | Tipos de archivo descargados    | Archivos: xlsx, csv                                                       |

---

## 🔮 Próximas Mejoras (Roadmap)

### Fase 1 - Optimización ✅

- [X] Paralelización de descargas con colas asíncronas de Playwright.
- [X] Bloqueo de recursos innecesarios y soporte completo de ejecución *Headless*.
- [X] Búsqueda optimizada contra el archivo Excel local del RPC sin raspar el inestable portal web viejo.

### Fase 2 - Robustez ✅

- [X] Reintentos automáticos en caso de timeout en SATyS.
- [X] Validación cruzada de identidad usando metadatos.
- [X] Clasificación inteligente de expedientes confusos y envío automático a `output_sin_operador/`.
- [X] Deshabilitación completa de la PARTE 2: Las capacidades de extracción e identificación ya funcionan nativamente en el flujo.

### Fase 3 - Interfaz y Reportes 🚀

- [X] **Interfaz gráfica completa (GUI) amigable con resúmenes detallados y botones interactivos.**
- [X] Programación y carga eficiente de folios desde un `.TXT`.
- [ ] Exportación de estadísticas en dashboard interactivo.
- [ ] Soporte para reanudar el último punto exacto en caso de apagón o cierre abrupto de PC.

---

## 👤 Autor

**Proyecto desarrollado para:**

- Instituto Federal de Telecomunicaciones (IFT)
- Coordinación General de Planeación Estratégica
- Dirección General Adjunta de Estadística y Análisis de Indicadores

**Desarrollador Original:** David Palestina Ramirez
**Actualizaciones y UI:** Equipo de Automatización
**Contacto:** david.palestina@ift.org.mx

---

## 📄 Licencia

Este proyecto es propiedad del Instituto Federal de Telecomunicaciones (IFT). Uso interno exclusivamente.
