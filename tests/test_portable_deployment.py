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

    def test_example_config_is_portable(self):
        data = json.loads((ROOT / "config" / "configuracion_local.example.json").read_text(encoding="utf-8"))
        self.assertEqual(data["rutas"]["carpeta_compartida"], "shared")
        self.assertEqual(data["rutas"]["excel"], "TrámitesCRT.xlsx")

    def test_environment_can_override_shared_path_and_credentials(self):
        code = r'''
import os
os.environ["SATYS_USUARIO"] = "env-user"
os.environ["SATYS_PASSWORD"] = "env-pass"
os.environ["SATYS_SHARED_DIR"] = "/tmp/satys-shared-test"
import configuracion_local as c
assert c.credenciales_satys() == ("env-user", "env-pass")
assert str(c.carpeta_compartida()) == "/tmp/satys-shared-test"
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
        self.assertNotIn("password=", text.lower())
        self.assertNotIn("app_password=", text.lower())

    def test_playwright_package_and_image_versions_match(self):
        req = (ROOT / "requirements-linux.lock.txt").read_text(encoding="utf-8")
        dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
        self.assertIn("playwright==1.57.0", req)
        self.assertIn("ARG PLAYWRIGHT_VERSION=1.57.0", dockerfile)


if __name__ == "__main__":
    unittest.main()
