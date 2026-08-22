#!/usr/bin/env python3
"""Construye una release SATyS sin secretos ni datos operativos."""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import subprocess
import tarfile
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath


ROOT_NAME = "Automatizacion-SATyS"
RUNTIME_DIRS = {
    ".git",
    ".idea",
    ".vscode",
    "__pycache__",
    "base_de_datos_rpc",
    "debug",
    "descargas",
    "exports",
    "logs",
    "output",
    "registros_diarios",
    "registros_fallidos",
    "releases",
    "runs",
    "Screenshots",
}
PROHIBITED_FILES = {
    ".env",
    ".satys_previous_image",
    "config/configuracion_local.json",
    "config/sesion_satys.json",
    "sesion_guardada.json",
}
ROOT_FILES = {
    ".dockerignore",
    ".gitignore",
    ".python-version",
    ".env.example",
    "ARCHIVOS_NO_INCLUIDOS.txt",
    "Dockerfile",
    "VERSION",
    "docker-compose.yml",
    "pyproject.toml",
    "requirements-dev.txt",
    "requirements-linux.in",
    "requirements-linux.lock.txt",
    "requirements-linux.txt",
    "ruta_carpeta_compartida.txt",
}
INCLUDED_DIRS = (".github", "config", "deploy", "docs", "scripts", "systemd", "tests", "web")
REQUIRED_MEMBERS = {
    "Parte1_descarga.py",
    "Parte4_excel.py",
    "automatizar_registros_diario.py",
    "estado_descargas.py",
    "extraer_registros_documentos.py",
    "guardado_seguro.py",
    "main_procesar.py",
    "scripts/desplegar_release_completa.sh",
    "scripts/instalar_linux_1am.sh",
    "scripts/preflight_despliegue.sh",
    "scripts/run_satys_diario.sh",
    "scripts/run_satys_internos.sh",
    "scripts/run_satys_internos_nuevos.sh",
    "scripts/procesar_folio_internos.sh",
    "scripts/procesar_folio_internos.ps1",
    "scripts/smoke_internos.py",
    "tests/test_internos_diario.py",
    "tests/test_guardado_seguro.py",
    "config/configuracion_local.example.json",
    "deploy/nginx-satys.conf",
    "Dockerfile",
    "docker-compose.yml",
    "requirements-linux.in",
    "requirements-linux.lock.txt",
    "pyproject.toml",
    "docs/API.md",
    "docs/BACKLOG_IMPLEMENTACION.md",
    "docs/BACKLOG_PRODUCCION_SATYS.md",
    "docs/GLOSARIO.md",
    "docs/ARQUITECTURA.md",
    "CONTRIBUTING.md",
    "CHANGELOG.md",
    "DESPLIEGUE_NUEVO.md",
    "scripts/actualizar_lock.sh",
    "scripts/docker_preflight.sh",
    "scripts/migrar_runtime_existente.sh",
    "scripts/desplegar_docker.sh",
    "scripts/rollback_docker.sh",
    "systemd/satys-docker-diario.service",
    "systemd/satys-docker-diario.timer",
    "tests/test_api_v1.py",
    ".env.example",
    "QUICKSTART_PORTABLE.md",
    "docs/PORTABILIDAD.md",
    "deploy/srvmbcudaqa01.env.example",
    "scripts/bootstrap_portable.sh",
    "scripts/doctor_portable.sh",
    "scripts/satys.sh",
    "scripts/docker_satys.sh",
    "scripts/podman_satys.sh",
    "scripts/instalar_container_systemd.sh",
    "scripts/satys.ps1",
    "tests/test_portable_deployment.py",
}


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def permitido(rel: Path) -> bool:
    posix = rel.as_posix()
    if posix in PROHIBITED_FILES:
        return False
    if any(part in RUNTIME_DIRS for part in rel.parts):
        return False
    if rel.name.startswith("~$"):
        return False
    if rel.suffix.lower() in {".xlsx", ".xls", ".zip", ".gz", ".pyc", ".pyo"}:
        return False
    return True


def archivos_release(project_root: Path) -> list[Path]:
    candidatos: set[Path] = set()
    candidatos.update(path for path in project_root.glob("*.py") if path.is_file())
    candidatos.update(path for path in project_root.glob("*.md") if path.is_file())
    candidatos.update(project_root / name for name in ROOT_FILES)
    for dirname in INCLUDED_DIRS:
        base = project_root / dirname
        if base.exists():
            candidatos.update(path for path in base.rglob("*") if path.is_file())

    resultado = []
    for path in candidatos:
        if not path.exists():
            raise FileNotFoundError(f"Falta archivo requerido por el empaquetador: {path}")
        rel = path.relative_to(project_root)
        # Evita incluir borradores Markdown vacíos que puedan existir en el
        # workspace local sin formar parte del producto desplegable.
        if permitido(rel) and not (path.suffix.lower() == ".md" and path.stat().st_size == 0):
            resultado.append(path)
    return sorted(resultado, key=lambda item: item.relative_to(project_root).as_posix())


def agregar_bytes(tar: tarfile.TarFile, arcname: str, data: bytes, mode: int = 0o644) -> None:
    info = tarfile.TarInfo(arcname)
    info.size = len(data)
    info.mode = mode
    info.uid = 0
    info.gid = 0
    info.uname = "root"
    info.gname = "root"
    info.mtime = 0
    tar.addfile(info, io.BytesIO(data))


def git_commit(project_root: Path) -> str:
    env_commit = os.getenv("SATYS_GIT_COMMIT", "").strip()
    if env_commit:
        return env_commit
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=project_root,
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout.strip()
    except Exception:
        pass
    return "unknown"


def construir(project_root: Path, output_dir: Path) -> tuple[Path, Path]:
    version = (project_root / "VERSION").read_text(encoding="utf-8").strip()
    if not version:
        raise ValueError("VERSION esta vacio")

    archivos = archivos_release(project_root)
    relativos = {path.relative_to(project_root).as_posix() for path in archivos}
    faltantes = sorted(REQUIRED_MEMBERS - relativos)
    if faltantes:
        raise ValueError("Release incompleta; faltan: " + ", ".join(faltantes))

    manifest_files = []
    for path in archivos:
        rel = path.relative_to(project_root).as_posix()
        data = path.read_bytes()
        manifest_files.append({"path": rel, "bytes": len(data), "sha256": sha256_bytes(data)})

    manifest = {
        "product": "Automatizacion-SATyS",
        "version": version,
        "git_commit": git_commit(project_root),
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "contains_secrets": False,
        "file_count": len(manifest_files),
        "files": manifest_files,
    }
    manifest_data = (json.dumps(manifest, ensure_ascii=False, indent=2) + "\n").encode("utf-8")

    output_dir.mkdir(parents=True, exist_ok=True)
    archive = output_dir / f"Automatizacion-SATyS-{version}.tar.gz"
    temporal = archive.with_suffix(archive.suffix + ".tmp")
    with tarfile.open(temporal, "w:gz", format=tarfile.PAX_FORMAT) as tar:
        for path in archivos:
            rel = path.relative_to(project_root).as_posix()
            data = path.read_bytes()
            mode = 0o755 if path.suffix in {".sh", ".py"} and "scripts" in path.parts else 0o644
            agregar_bytes(tar, f"{ROOT_NAME}/{rel}", data, mode=mode)
        agregar_bytes(tar, f"{ROOT_NAME}/DEPLOYMENT_MANIFEST.json", manifest_data)
    os.replace(temporal, archive)

    checksum = archive.with_suffix(archive.suffix + ".sha256")
    checksum.write_text(f"{sha256_file(archive)}  {archive.name}\n", encoding="ascii")
    verificar(archive)
    return archive, checksum


def verificar(archive: Path) -> None:
    with tarfile.open(archive, "r:gz") as tar:
        members = [member for member in tar.getmembers() if member.isfile()]
        members_by_name = {member.name: member for member in members}
        names = {member.name for member in members}
        for member in members:
            rel = PurePosixPath(member.name)
            if rel.is_absolute() or ".." in rel.parts:
                raise ValueError(f"Ruta insegura en el paquete: {member.name}")
            if not member.name.startswith(f"{ROOT_NAME}/"):
                raise ValueError(f"Miembro fuera de la raiz esperada: {member.name}")

        normalized = {
            name.removeprefix(f"{ROOT_NAME}/")
            for name in names
            if name.startswith(f"{ROOT_NAME}/")
        }
        prohibidos = sorted(PROHIBITED_FILES & normalized)
        if prohibidos:
            raise ValueError("El paquete contiene archivos prohibidos: " + ", ".join(prohibidos))
        faltantes = sorted(REQUIRED_MEMBERS - normalized)
        if faltantes:
            raise ValueError("El paquete no contiene: " + ", ".join(faltantes))
        if "DEPLOYMENT_MANIFEST.json" not in normalized:
            raise ValueError("Falta DEPLOYMENT_MANIFEST.json")

        manifest_member = members_by_name[f"{ROOT_NAME}/DEPLOYMENT_MANIFEST.json"]
        manifest_stream = tar.extractfile(manifest_member)
        if manifest_stream is None:
            raise ValueError("No se pudo leer DEPLOYMENT_MANIFEST.json")
        manifest = json.loads(manifest_stream.read().decode("utf-8"))
        manifest_files = manifest.get("files", [])
        if manifest.get("contains_secrets") is not False:
            raise ValueError("El manifest no declara contains_secrets=false")
        if manifest.get("file_count") != len(manifest_files):
            raise ValueError("file_count no coincide con la lista del manifest")

        expected_paths = set()
        for item in manifest_files:
            rel = str(item.get("path", ""))
            member_name = f"{ROOT_NAME}/{rel}"
            expected_paths.add(rel)
            member = members_by_name.get(member_name)
            if member is None:
                raise ValueError(f"Falta el archivo declarado en manifest: {rel}")
            stream = tar.extractfile(member)
            if stream is None:
                raise ValueError(f"No se pudo leer el archivo del manifest: {rel}")
            data = stream.read()
            if len(data) != item.get("bytes") or sha256_bytes(data) != item.get("sha256"):
                raise ValueError(f"Hash o tamano no coincide con manifest: {rel}")

        packaged_paths = normalized - {"DEPLOYMENT_MANIFEST.json"}
        if packaged_paths != expected_paths:
            extras = sorted(packaged_paths - expected_paths)
            raise ValueError("Hay archivos no declarados en manifest: " + ", ".join(extras))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--output-dir", type=Path, default=None)
    parser.add_argument("--verify-only", type=Path, default=None)
    args = parser.parse_args()

    if args.verify_only:
        verificar(args.verify_only.resolve())
        print(f"OK paquete verificado: {args.verify_only.resolve()}")
        return 0

    project_root = args.project_root.resolve()
    output_dir = (args.output_dir or (project_root / "releases")).resolve()
    archive, checksum = construir(project_root, output_dir)
    print(f"Release: {archive}")
    print(f"SHA-256: {checksum}")
    print("Contenido validado: sin credenciales, sesiones, Excel ni datos operativos")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
