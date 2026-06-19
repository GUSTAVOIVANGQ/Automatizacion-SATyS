# Roadmap: Mejoras Enterprise-Grade para SATyS

Para llevar el script a un nivel verdaderamente empresarial, el orden de implementación es fundamental. No podemos agregar concurrencia sin antes tener un sistema sólido de argumentos y logs, de lo contrario, depurar múltiples procesos a la vez sería un caos. 

A continuación presento el orden ideal de implementación, incluyendo **tres nuevas mejoras** que no habíamos contemplado pero que son estándar en la industria.

## Fase 1: Control, Configuración y Trazabilidad (Bases Sólidas)
*Estas mejoras no alteran la lógica central, pero hacen que el programa sea más fácil de usar, configurar y auditar.*

1. **CLI Profesional (Argumentos de Consola)**:
   - Implementar `argparse`. Permitirá correr comandos como: `python Parte1_descarga.py --folios 123 456 --headless --limite 10`.
   - **¿Por qué ahora?** Necesitamos esto listo para poder probar las fases siguientes rápidamente.
2. **[NUEVO] Externalización de Configuración (`.env` / `config.json`)**:
   - Mover todos los `TIMEOUTS`, `URLS`, selectores y configuración de carpetas fuera del código fuente.
   - **¿Por qué?** Permite que un usuario de negocio cambie un *timeout* sin tener que pedirle a un desarrollador que edite el código Python.
3. **[NUEVO] Logging Rotativo Estructurado**:
   - Actualmente los logs se imprimen en consola. Implementaremos `RotatingFileHandler` para guardar un histórico de logs (ej. `satys_execution.log`) de hasta 10MB por archivo, guardando los últimos 5.
   - **¿Por qué?** Cuando el programa corra de madrugada en un servidor, si algo falla, tendrás el archivo exacto para revisar qué pasó, sin saturar el disco duro.

## Fase 2: Resiliencia (Tolerancia a Fallos)
*El gobierno se cae, las redes fallan. El script no debe morir.*

4. **Sistema de Reintentos Automáticos (`tenacity`)**:
   - Envolver funciones críticas (navegación, clics problemáticos, descargas) con decoradores de reintento.
   - **Comportamiento**: Si la descarga del PDF de un folio se congela por un micro-corte de red, lo reintenta 3 veces esperando 5, 10 y 15 segundos respectivamente antes de marcarlo como "ERROR".

## Fase 3: Alto Rendimiento (El Gran Salto)
*Una vez que el script es estable, configurable y tolerante a fallos, lo hacemos rápido.*

> [!TIP]
> **Propuesta Arquitectónica para Concurrencia:**
> Originalmente propusimos migrar todo el código a `asyncio` (Async Playwright). Sin embargo, reescribir casi 3,000 líneas de código estable a asíncrono es altamente riesgoso. 
> **La alternativa más robusta y segura** es utilizar `concurrent.futures.ThreadPoolExecutor` con el API Síncrono actual. 
> Esto nos permite procesar N folios a la vez (ej. en grupos de 3) levantando contextos independientes en paralelo que comparten el mismo archivo de sesión (`sesion_guardada.json`). El resultado será idéntico (dividir el tiempo entre 3) pero manteniendo el código base 100% estable.

5. **Preparación de Contextos Independientes**:
   - Refactorizar el flujo para que el hilo principal valide la sesión una sola vez.
   - Cada hilo secundario levantará un contexto de Chromium usando `sesion_guardada.json`.
6. **Ejecución Concurrente Controlada**:
   - Procesar los N folios ingresados usando `ThreadPoolExecutor(max_workers=3)`.
   - **Resultado**: Los folios se procesan en paralelo. Si ingresas 10 folios, se irán despachando de 3 en 3 automáticamente.

## Fase 4: Entrega y Monitoreo (El Toque Final)
*El programa debe avisarte cuando termine, tú no deberías ir a revisarlo.*

7. **Notificaciones en Tiempo Real**:
   - Integración con Webhooks de Microsoft Teams, Slack o un simple SMTP para Correo Electrónico.
   - **Resultado**: Recibes un mensaje con el resumen y los folios que fallaron.
8. **[NUEVO] Dashboard de Resultados en HTML**:
   - En vez de solo exportar un `.json`, el script generará un `reporte_ejecucion.html` interactivo y visualmente agradable donde puedas filtrar los folios descargados, ver qué falló y abrir directamente los archivos con un clic.

---

> [!TIP]
> **Mi Sugerencia:**
> Recomiendo que empecemos por la **Fase 1**, ya que implementar la CLI (`argparse`) y los Logs Rotativos nos dará el control necesario para probar todo lo demás cómodamente. Una vez validada, pasamos a la resiliencia (Fase 2) y luego al verdadero reto: la asincronía (Fase 3).

## Open Questions
- ¿Estás de acuerdo con este orden de prioridades?
- Para la Fase 4 (Notificaciones), ¿cuál es el medio de comunicación preferido por tu equipo: **Microsoft Teams, Slack, Correo Electrónico u otro**?
- Para la Fase 1, ¿quieres que implementemos la configuración externa a través de un archivo `.env` o prefieres un `config.json`?

Si estás de acuerdo con el plan, haz clic en **Proceed** y empezaré de inmediato con la **Fase 1** (CLI, Configuración externa y Logs Rotativos).
