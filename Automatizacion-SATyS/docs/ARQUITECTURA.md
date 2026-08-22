# Arquitectura

```mermaid
flowchart LR
    SATYS["SATyS interno\nsatys.ift.org.mx"] --> P1["Parte 1\nPlaywright / descarga"]
    P1 --> D[(descargas/)]
    P1 --> P3["Parte 3\nRPC"]
    RPC[(base_de_datos_rpc/)] --> P3
    P3 --> P4["Parte 4\nExcel y organización"]
    P4 --> O[(output/)]
    P4 --> X["TrámitesCRT.xlsx"]
    X --> API["FastAPI\n/api/v1/*"]
    O --> API
    D --> API
    API --> NGINX["nginx TLS\n127.0.0.1:8082"]
    TIMER["systemd timer\n01:00 America/Mexico_City"] --> WORKER["satys-worker"]
    WORKER --> SATYS
    WORKER --> L[(logs/)]
    L --> API
```

## Ejecución en contenedores

`satys-api` es de larga duración. `satys-worker` es efímero y se ejecuta mediante `docker compose run --rm satys-worker`. El timer Docker incluido en `systemd/satys-docker-diario.*` invoca ese worker una vez al día.

## Persistencia

Los directorios `descargas/`, `output/`, `logs/`, `runs/`, `exports/`, `base_de_datos_rpc/` y `registros_diarios/`, además de `TrámitesCRT.xlsx` y `config/configuracion_local.json`, se montan desde el host y no se hornean en la imagen.
