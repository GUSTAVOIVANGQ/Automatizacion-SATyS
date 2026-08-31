from __future__ import annotations

import json
import os
import subprocess
import tempfile
import unittest
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class PortableDeploymentTests(unittest.TestCase):
    def test_compose_has_no_institutional_host_path(self):
        text = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")
        self.assertNotIn("/data/gustavo.garcia", text)
        self.assertNotIn("/depi/dgp", text)
        self.assertNotIn("/depi/DEI_DATOS", text)
        self.assertIn("SATYS_RUNTIME_DIR", text)
        self.assertIn("SATYS_SHARED_HOST_DIR", text)
        self.assertIn("SATYS_CONFIG_HOST_FILE", text)
        self.assertIn('${SATYS_API_BIND:-127.0.0.1}:${SATYS_API_PORT:-8082}:8082', text)
        self.assertIn('${SATYS_INTERNOS_WORKERS:-12}', text)
        self.assertIn('${SATYS_INTERNOS_WORKER_REINTENTOS:-2}', text)
        self.assertIn('${SATYS_ZIP_MAX_ITERACIONES:-32}', text)
        self.assertIn('${SATYS_ZIP_RUTA_RELATIVA_MAX:-140}', text)
        self.assertIn('${SATYS_SHM_SIZE:-6gb}', text)

    def test_example_config_is_portable(self):
        data = json.loads((ROOT / "config" / "configuracion_local.example.json").read_text(encoding="utf-8"))
        self.assertEqual(data["rutas"]["carpeta_compartida"], "shared")
        self.assertEqual(data["rutas"]["excel"], "TrámitesCRT.xlsx")
        self.assertEqual(data["procesamiento"]["internos_workers"], 12)

    def test_environment_can_override_shared_path_and_credentials(self):
        code = r'''
import os
from pathlib import Path
os.environ["SATYS_USUARIO"] = "env-user"
os.environ["SATYS_PASSWORD"] = "env-pass"
os.environ["SATYS_SHARED_DIR"] = str(Path.cwd() / "runtime" / "shared-env-test")
os.environ["SATYS_INTERNOS_WORKERS"] = "24"
import configuracion_local as c
assert c.credenciales_satys() == ("env-user", "env-pass")
assert c.carpeta_compartida() == Path(os.environ["SATYS_SHARED_DIR"])
assert c.configuracion_procesamiento()["internos_workers"] == 24
print("OK")
'''
        result = subprocess.run(
            [sys.executable, "-c", code],
            cwd=ROOT,
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("OK", result.stdout)

    def test_server_profile_reuses_existing_runtime_without_secrets(self):
        text = (ROOT / "deploy" / "srvmbcudaqa01.env.example").read_text(encoding="utf-8")
        self.assertIn("SATYS_RUNTIME_DIR=/data/gustavo.garcia/satys/Automatizacion-SATyS", text)
        self.assertIn("SATYS_SHARED_HOST_DIR=/depi/dgp/DEI_DATOS/SATyS", text)
        self.assertIn("SATYS_API_BIND=0.0.0.0", text)
        self.assertIn("SATYS_API_NETWORK=slirp4netns:enable_ipv6=false", text)
        self.assertIn("SATYS_INTERNOS_WORKERS=12", text)
        self.assertIn("SATYS_INTERNOS_WORKER_REINTENTOS=2", text)
        self.assertIn("SATYS_ZIP_MAX_ITERACIONES=32", text)
        self.assertIn("SATYS_ZIP_RUTA_RELATIVA_MAX=140", text)
        self.assertIn("SATYS_SHM_SIZE=6gb", text)
        self.assertNotIn("password=", text.lower())
        self.assertNotIn("app_password=", text.lower())

    def test_playwright_package_and_image_versions_match(self):
        req = (ROOT / "requirements-linux.lock.txt").read_text(encoding="utf-8")
        dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
        self.assertIn("playwright==1.57.0", req)
        self.assertIn("ARG PLAYWRIGHT_VERSION=1.57.0", dockerfile)

    def test_systemd_supervisa_api_y_expone_internos_manual(self):
        text = (ROOT / "scripts" / "instalar_container_systemd.sh").read_text(encoding="utf-8")
        podman = (ROOT / "scripts" / "podman_satys.sh").read_text(encoding="utf-8")
        self.assertIn('Type=simple', text)
        self.assertIn('Restart=on-failure', text)
        self.assertIn('satys-container-internos.service', text)
        self.assertIn('scripts/satys.sh internos', text)
        self.assertIn('api-run)', podman)
        self.assertIn('podman run --rm --name satys-api', podman)
        self.assertIn('sin-operador-rpc)', podman)
        self.assertIn('resolver_sin_operador_rpc_publico.py', podman)
        self.assertIn('SATYS_SIN_OPERADOR_RPC_PUBLICO_TIMEOUT', podman)


if __name__ == "__main__":
    unittest.main()
