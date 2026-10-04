from __future__ import annotations

import json
import tempfile
import threading
import unittest
from http.client import HTTPConnection
from pathlib import Path
from typing import Any

from PIL import Image

from server import AgentHTTPServer


class AgentServerTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        root = Path(self.temporary_directory.name)
        self.token = "test-token"

        def fake_processor(source: Path, scene_dir: Path, install_dir: Path, install: bool) -> dict[str, Any]:
            scene_dir.mkdir(parents=True)
            (scene_dir / "layers").mkdir()
            (scene_dir / "posterized.png").write_bytes(b"posterized")
            (scene_dir / "contact-sheet.png").write_bytes(b"contact sheet")
            Image.new("L", (2, 2), 255).save(scene_dir / "layers" / "01_darkest.png")
            return {
                "method": {"engine": "fake GIMP MCP"},
                "width": 2,
                "height": 2,
                "masks": [
                    {
                        "index": 1,
                        "name": "darkest",
                        "path": "layers/01_darkest.png",
                        "target_rgb": [12, 24, 36],
                        "coverage": 1.0,
                    }
                ],
            }

        self.server = AgentHTTPServer(
            ("127.0.0.1", 0),
            self.token,
            root / "jobs",
            root / "gimp-mcp",
            False,
            processor=fake_processor,
        )
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.connection = HTTPConnection("127.0.0.1", self.server.server_port, timeout=5)

    def tearDown(self) -> None:
        self.connection.close()
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)
        self.temporary_directory.cleanup()

    def request(self, method: str, path: str, body: bytes = b"", headers: dict[str, str] | None = None):
        self.connection.request(method, path, body=body, headers=headers or {})
        response = self.connection.getresponse()
        return response.status, response.getheaders(), response.read()

    def test_health_is_public_but_processing_requires_bearer_token(self) -> None:
        status, _, body = self.request("GET", "/healthz")
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["status"], "ok")

        status, _, _ = self.request("POST", "/process", b"image", {"Content-Type": "image/png"})
        self.assertEqual(status, 401)

    def test_rejects_unsupported_media_type(self) -> None:
        status, _, body = self.request(
            "POST",
            "/process",
            b"image",
            {"Authorization": "Bearer test-token", "Content-Type": "application/octet-stream"},
        )
        self.assertEqual(status, 415)
        self.assertIn("supported", json.loads(body))

    def test_returns_steps_and_serves_generated_mask(self) -> None:
        image = tempfile.NamedTemporaryFile(suffix=".png", delete=False)
        image.close()
        try:
            Image.new("RGB", (2, 2), (10, 20, 30)).save(image.name)
            payload = Path(image.name).read_bytes()
        finally:
            Path(image.name).unlink()

        status, _, body = self.request(
            "POST",
            "/process",
            payload,
            {
                "Authorization": "Bearer test-token",
                "Content-Type": "image/png",
                "Content-Length": str(len(payload)),
                "X-Filename": "reference.png",
            },
        )
        self.assertEqual(status, 200)
        result = json.loads(body)
        self.assertEqual(result["engine"], "fake GIMP MCP")
        self.assertEqual(result["steps"][0]["name"], "darkest")
        self.assertTrue(result["steps"][0]["path"].startswith(f"/jobs/{result['job_id']}/scene/"))

        status, headers, mask = self.request(
            "GET",
            result["steps"][0]["path"],
            headers={"Authorization": "Bearer test-token"},
        )
        self.assertEqual(status, 200)
        self.assertEqual(dict(headers)["Content-Type"], "image/png")
        self.assertGreater(len(mask), 0)

        status, _, _ = self.request(
            "GET",
            f"/jobs/{result['job_id']}/scene/../public.json",
            headers={"Authorization": "Bearer test-token"},
        )
        self.assertEqual(status, 404)


if __name__ == "__main__":
    unittest.main()
