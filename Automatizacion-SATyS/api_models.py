"""Modelos Pydantic públicos de la API SATyS v1.

Los modelos usan ``extra='allow'`` en estados operativos porque los JSON de las
corridas evolucionan con el pipeline. Así OpenAPI documenta los campos estables
sin bloquear campos diagnósticos nuevos.
"""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class FlexibleModel(BaseModel):
    model_config = ConfigDict(extra="allow")


class ErrorResponse(BaseModel):
    detail: str
    code: str


class HealthResponse(FlexibleModel):
    ok: bool
    project: str
    project_dir: str
    logs_dir: str
    estado_json: str
    manual_allowed: bool
    repair_allowed: bool
    start_allowed: bool
    timer_edit_allowed: bool


class ConfigResponse(HealthResponse):
    timer_hora: str | None = None
    workers: int | None = None
    headless: bool | None = None


class StateResponse(FlexibleModel):
    running: bool = False
    stage: str | None = None
    mensaje: str | None = None
    ok: bool | None = None


class RunSummaryResponse(FlexibleModel):
    ok: bool | None = None
    mensaje: str | None = None
    fecha_ejecucion: str | None = None


class SystemdStatusResponse(FlexibleModel):
    service: str | None = None
    timer: str | None = None


class FileInfo(FlexibleModel):
    exists: bool | None = None
    path: str | None = None
    name: str | None = None
    size: int | None = None
    modified_at: str | None = None


class FilesResponse(FlexibleModel):
    excel_control: dict[str, Any]
    excel_consolidado: dict[str, Any]
    output: dict[str, Any]
    descargas: dict[str, Any]
    logs: dict[str, Any]
    registros_diarios: dict[str, Any]


class HistoryResponse(FlexibleModel):
    daily: list[dict[str, Any]] = Field(default_factory=list)
    manual: list[dict[str, Any]] = Field(default_factory=list)


class RegistroPathInfo(FlexibleModel):
    tipo: str
    raiz: str
    path: str
    relpath: str
    name: str
    size: int | None = None
    modified_at: str


class RegistroSearchResponse(BaseModel):
    ok: bool
    registro: str
    tipo: str
    total: int
    items: list[RegistroPathInfo]


class ProcessStateResponse(FlexibleModel):
    running: bool = False
    ok: bool | None = None
    pid: int | None = None
    mensaje: str | None = None
    run_id: str | None = None


class RepairStateResponse(ProcessStateResponse):
    status: str | None = None
    summary: dict[str, Any] | None = None


class RepairStartRequest(BaseModel):
    reiniciar_cola: bool = False
    actualizar_salidas: bool = True
    redescargar_archivos: bool = False
    reintentos: int = Field(default=2, ge=0, le=10)


class TimerUpdateRequest(BaseModel):
    hora: str = Field(pattern=r"^([01]\d|2[0-3]):[0-5]\d$")


class TimerUpdateResponse(FlexibleModel):
    ok: bool
    hora: str
    install: dict[str, Any]
    systemd: dict[str, Any]


class ProcessStartResponse(FlexibleModel):
    ok: bool
    service: str
    estado: dict[str, Any]


class VersionResponse(BaseModel):
    version: str
    git_commit: str
    git_source: str
