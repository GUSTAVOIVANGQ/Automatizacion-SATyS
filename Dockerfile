# syntax=docker/dockerfile:1.7
ARG PLAYWRIGHT_VERSION=1.57.0
FROM mcr.microsoft.com/playwright/python:v${PLAYWRIGHT_VERSION}-noble AS builder
WORKDIR /build
COPY requirements-linux.lock.txt ./
# La imagen oficial de Playwright para Ubuntu Noble no incluye ensurepip/venv.
# Instalamos el paquete venv de la misma versión de Python antes de crear el entorno.
RUN apt-get update \
 && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends python3.12-venv \
 && rm -rf /var/lib/apt/lists/*
RUN python -m venv /opt/satys-venv \
 && /opt/satys-venv/bin/python -m pip install --upgrade pip \
 && /opt/satys-venv/bin/pip install --no-cache-dir -r requirements-linux.lock.txt

FROM mcr.microsoft.com/playwright/python:v${PLAYWRIGHT_VERSION}-noble AS runtime
ARG SATYS_VERSION=dev
ARG SATYS_GIT_COMMIT=unknown
ENV PATH="/opt/satys-venv/bin:${PATH}" \
    PYTHONUNBUFFERED=1 \
    PYTHONIOENCODING=utf-8 \
    TZ=America/Mexico_City \
    SATYS_VERSION=${SATYS_VERSION} \
    SATYS_GIT_COMMIT=${SATYS_GIT_COMMIT} \
    SATYS_DEPLOYMENT_MODE=docker \
    PLAYWRIGHT_BROWSERS_PATH=/ms-playwright
WORKDIR /app
COPY --from=builder /opt/satys-venv /opt/satys-venv
COPY . /app
RUN mkdir -p descargas output logs runs exports base_de_datos_rpc registros_diarios registros_fallidos \
 && useradd --create-home --uid 10001 satys \
 && chown -R satys:satys /app
USER satys
EXPOSE 8082
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8082/api/health', timeout=4).read()" || exit 1
CMD ["uvicorn", "satys_api:app", "--host", "0.0.0.0", "--port", "8082", "--proxy-headers"]
