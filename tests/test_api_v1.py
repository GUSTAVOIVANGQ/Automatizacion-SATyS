import os
import unittest
from unittest.mock import patch

os.environ.setdefault("SATYS_API_ALLOW_MANUAL", "0")
os.environ.setdefault("SATYS_API_ALLOW_REPAIR", "0")
os.environ.setdefault("SATYS_API_ALLOW_START", "0")
os.environ.setdefault("SATYS_API_ALLOW_TIMER_EDIT", "0")

from fastapi.testclient import TestClient

import satys_api


class ApiV1Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(satys_api.app)

    def test_health_canonical_and_legacy(self):
        for path in ("/api/v1/health", "/api/health"):
            response = self.client.get(path)
            self.assertEqual(response.status_code, 200)
            self.assertTrue(response.json()["ok"])

    def test_version_endpoint(self):
        response = self.client.get("/api/v1/version")
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertIn("version", body)
        self.assertIn("git_commit", body)
        self.assertIn("git_source", body)

    def test_openapi_only_exposes_v1_api(self):
        schema = self.client.get("/openapi.json").json()
        paths = schema["paths"]
        self.assertIn("/api/v1/health", paths)
        self.assertIn("/api/v1/version", paths)
        self.assertNotIn("/api/config", paths)
        self.assertNotIn("/api/estado", paths)

    def test_http_exception_shape_is_standardized(self):
        response = self.client.get("/api/v1/resumen/ultimo")
        if response.status_code == 404:
            self.assertEqual(set(response.json()), {"detail", "code"})
            self.assertEqual(response.json()["code"], "not_found")

    def test_manual_disabled_returns_standard_error(self):
        with patch.dict(os.environ, {"SATYS_API_ALLOW_MANUAL": "0"}):
            response = self.client.post(
                "/api/v1/manual/procesar",
                files={"archivo": ("registros.txt", b"CRT26-000001\n", "text/plain")},
                data={"tipo_txt": "registros", "workers": "1", "headless": "true"},
            )
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()["code"], "forbidden")

    def test_dashboard_links_to_api_docs(self):
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        self.assertIn('href="/docs"', response.text)
        self.assertIn("API Docs", response.text)

    def test_custom_docs_ui(self):
        response = self.client.get("/docs")
        self.assertEqual(response.status_code, 200)
        self.assertIn("docs-appbar", response.text)
        self.assertIn("Documentación para desarrolladores", response.text)
        self.assertIn("swagger-ui", response.text)
        self.assertIn('/static/docs-theme.js', response.text)
        self.assertIn('id="docs-theme-toggle"', response.text)
        self.assertIn("localStorage.getItem('theme')", response.text)

    def test_validation_error_is_standardized(self):
        response = self.client.post("/api/v1/timer/hora", json={})
        self.assertEqual(response.status_code, 422)
        payload = response.json()
        self.assertEqual(payload.get("code"), "validation_error")
        self.assertIsInstance(payload.get("detail"), str)


if __name__ == "__main__":
    unittest.main()
