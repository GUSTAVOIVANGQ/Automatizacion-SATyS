from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class Api8082SecurityTests(unittest.TestCase):
    def test_installer_uses_8082_and_loopback(self):
        text = (ROOT / "scripts" / "instalar_linux_1am.sh").read_text(encoding="utf-8")
        self.assertIn("API_PORT=8082", text)
        self.assertIn("--host 127.0.0.1 --port \"$API_PORT\"", text)
        self.assertNotIn("--host 0.0.0.0", text)

    def test_full_deployer_defaults_to_8082(self):
        text = (ROOT / "scripts" / "desplegar_release_completa.sh").read_text(encoding="utf-8")
        self.assertIn('API_PORT="8082"', text)
        self.assertNotIn('API_PORT="8095"', text)

    def test_nginx_proxies_only_to_loopback_8082(self):
        text = (ROOT / "deploy" / "nginx-satys.conf").read_text(encoding="utf-8")
        self.assertIn("proxy_pass http://127.0.0.1:8082;", text)
        self.assertIn("listen 443 ssl", text)

    def test_gitignore_keeps_frontend_template_trackable(self):
        gitignore = (ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()
        self.assertNotIn("*.html", gitignore)
        self.assertIn("debug_html/", gitignore)
        self.assertTrue((ROOT / "web" / "templates" / "index.html").is_file())

    def test_docker_compose_binds_only_loopback_and_mounts_runtime_secret(self):
        text = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")
        self.assertIn('"127.0.0.1:${SATYS_API_PORT:-8082}:8082"', text)
        self.assertIn('${SATYS_CONFIG_HOST_FILE:-./config/configuracion_local.json}:/app/config/configuracion_local.json:ro', text)
        self.assertIn('user: "${SATYS_UID:-10001}:${SATYS_GID:-10001}"', text)
        self.assertIn('SATYS_SHARED_HOST_DIR', text)
        self.assertNotIn('/depi/DEI_DATOS/SATyS:/depi/DEI_DATOS/SATyS', text)

    def test_dockerignore_excludes_runtime_secrets_and_data(self):
        lines = (ROOT / ".dockerignore").read_text(encoding="utf-8").splitlines()
        for required in (
            "config/configuracion_local.json",
            "sesion_guardada.json",
            "TrámitesCRT.xlsx",
            "descargas/",
            "output/",
            "logs/",
            "runs/",
            "base_de_datos_rpc/",
            "registros_diarios/",
        ):
            self.assertIn(required, lines)


if __name__ == "__main__":
    unittest.main()
