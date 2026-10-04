from __future__ import annotations

import argparse
import json
import mimetypes
import os
import secrets
import shutil
import threading
import uuid
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable, Sequence
from urllib.parse import unquote, urlparse

from PIL import Image

from track3.decompose import decompose_scene
from track3.gimp_mcp_runtime import (
    McpClient,
    default_install_dir,
    gimp_service,
    install_runtime,
)

MAX_UPLOAD_BYTES = 25 * 1024 * 1024
IMAGE_TYPES = {
    "image/bmp": ".bmp",
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/tiff": ".tiff",
    "image/webp": ".webp",
}


class ProcessingError(RuntimeError):
    pass


def _json_bytes(value: Any) -> bytes:
    return (json.dumps(value, separators=(",", ":")) + "\n").encode("utf-8")


def _safe_filename(value: str, content_type: str) -> str:
    suffix = Path(value).suffix.lower()
    if suffix not in IMAGE_TYPES.values():
        suffix = IMAGE_TYPES.get(content_type, ".img")
    return f"source{suffix}"


def _read_token(token_file: Path) -> str:
    token_file = token_file.expanduser()
    if token_file.exists():
        token = token_file.read_text(encoding="utf-8").strip()
        if token:
            return token

    token_file.parent.mkdir(parents=True, exist_ok=True)
    token = secrets.token_urlsafe(32)
    temporary = token_file.with_name(f".{token_file.name}.{os.getpid()}.tmp")
    temporary.write_text(token + "\n", encoding="utf-8")
    temporary.chmod(0o600)
    os.replace(temporary, token_file)
    return token


def _validate_image(path: Path) -> None:
    try:
        with Image.open(path) as image:
            image.verify()
    except Exception as exc:
        raise ProcessingError(f"uploaded file is not a valid image: {exc}") from exc


def process_with_gimp(
    source: Path,
    scene_dir: Path,
    install_dir: Path,
    install: bool,
) -> dict[str, Any]:
    if install:
        install_runtime(install_dir)

    with gimp_service(install_dir):
        with McpClient(install_dir) as client:
            return decompose_scene(client, source, scene_dir)


def _public_manifest(manifest: dict[str, Any], job_id: str) -> dict[str, Any]:
    steps = []
    for mask in manifest.get("masks", []):
        step = dict(mask)
        step["path"] = f"/jobs/{job_id}/scene/{step['path']}"
        steps.append(step)
    return {
        "job_id": job_id,
        "engine": manifest.get("method", {}).get("engine"),
        "width": manifest.get("width"),
        "height": manifest.get("height"),
        "steps": steps,
        "artifacts": {
            "manifest": f"/jobs/{job_id}/scene/manifest.json",
            "posterized": f"/jobs/{job_id}/scene/posterized.png",
            "contact_sheet": f"/jobs/{job_id}/scene/contact-sheet.png",
        },
    }


class AgentHTTPServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(
        self,
        address: tuple[str, int],
        token: str,
        output_root: Path,
        install_dir: Path,
        install: bool,
        processor: Callable[[Path, Path, Path, bool], dict[str, Any]] = process_with_gimp,
    ) -> None:
        super().__init__(address, AgentRequestHandler)
        self.token = token
        self.output_root = output_root.resolve()
        self.install_dir = install_dir.resolve()
        self.install = install
        self.processor = processor
        self.process_lock = threading.Lock()
        self.output_root.mkdir(parents=True, exist_ok=True)


class AgentRequestHandler(BaseHTTPRequestHandler):
    server: AgentHTTPServer
    protocol_version = "HTTP/1.1"

    def _send_json(self, status: int, value: Any) -> None:
        payload = _json_bytes(value)
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(payload)

    def _authorized(self) -> bool:
        header = self.headers.get("Authorization", "")
        return secrets.compare_digest(header, f"Bearer {self.server.token}")

    def _require_auth(self) -> bool:
        if self._authorized():
            return True
        self._send_json(HTTPStatus.UNAUTHORIZED, {"error": "unauthorized"})
        return False

    def do_GET(self) -> None:
        path = urlparse(self.path).path
        if path == "/healthz":
            self._send_json(HTTPStatus.OK, {"status": "ok", "service": "rob-boss-agent"})
            return
        if not self._require_auth():
            return
        if path == "/":
            self._send_json(
                HTTPStatus.OK,
                {
                    "service": "rob-boss-agent",
                    "endpoints": {
                        "process": "POST /process",
                        "artifact": "GET /jobs/{job_id}/{path}",
                    },
                },
            )
            return
        self._serve_artifact(path)

    def do_POST(self) -> None:
        if urlparse(self.path).path != "/process":
            self._send_json(HTTPStatus.NOT_FOUND, {"error": "not found"})
            return
        if not self._require_auth():
            return

        content_type = self.headers.get_content_type()
        if content_type not in IMAGE_TYPES:
            self._send_json(
                HTTPStatus.UNSUPPORTED_MEDIA_TYPE,
                {"error": "Content-Type must be one of the supported image types", "supported": sorted(IMAGE_TYPES)},
            )
            return

        raw_length = self.headers.get("Content-Length")
        try:
            content_length = int(raw_length) if raw_length is not None else -1
        except ValueError:
            content_length = -1
        if content_length < 1:
            self._send_json(HTTPStatus.LENGTH_REQUIRED, {"error": "Content-Length is required"})
            return
        if content_length > MAX_UPLOAD_BYTES:
            self._send_json(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, {"error": "image exceeds 25 MiB limit"})
            return

        job_id = uuid.uuid4().hex
        job_dir = self.server.output_root / job_id
        job_dir.mkdir(parents=True)
        source = job_dir / _safe_filename(self.headers.get("X-Filename", "source"), content_type)
        try:
            with source.open("wb") as handle:
                remaining = content_length
                while remaining:
                    chunk = self.rfile.read(min(1024 * 1024, remaining))
                    if not chunk:
                        raise ProcessingError("request body ended before Content-Length")
                    handle.write(chunk)
                    remaining -= len(chunk)
            _validate_image(source)
            with self.server.process_lock:
                manifest = self.server.processor(
                    source,
                    job_dir / "scene",
                    self.server.install_dir,
                    self.server.install,
                )
            public = _public_manifest(manifest, job_id)
            (job_dir / "public.json").write_bytes(_json_bytes(public))
            self._send_json(HTTPStatus.OK, public)
        except ProcessingError as exc:
            shutil.rmtree(job_dir, ignore_errors=True)
            self._send_json(HTTPStatus.BAD_REQUEST, {"error": str(exc)})
        except Exception as exc:
            shutil.rmtree(job_dir, ignore_errors=True)
            self._send_json(HTTPStatus.INTERNAL_SERVER_ERROR, {"error": str(exc)})

    def _serve_artifact(self, path: str) -> None:
        parts = [unquote(part) for part in path.split("/") if part]
        if (
            len(parts) < 3
            or parts[0] != "jobs"
            or any(part in {".", ".."} for part in parts)
        ):
            self._send_json(HTTPStatus.NOT_FOUND, {"error": "not found"})
            return
        job_id = parts[1]
        if len(job_id) != 32 or any(char not in "0123456789abcdef" for char in job_id):
            self._send_json(HTTPStatus.NOT_FOUND, {"error": "not found"})
            return
        candidate = (self.server.output_root / job_id / Path(*parts[2:])).resolve()
        job_root = (self.server.output_root / job_id).resolve()
        if job_root not in candidate.parents and candidate != job_root:
            self._send_json(HTTPStatus.NOT_FOUND, {"error": "not found"})
            return
        if not candidate.is_file():
            self._send_json(HTTPStatus.NOT_FOUND, {"error": "not found"})
            return
        payload = candidate.read_bytes()
        content_type = mimetypes.guess_type(candidate.name)[0] or "application/octet-stream"
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, format: str, *args: Any) -> None:
        print(f"[{self.log_date_time_string()}] {format % args}", flush=True)


def run_server(arguments: argparse.Namespace) -> None:
    token_file = arguments.token_file.expanduser()
    token = os.environ.get("AGENT_API_TOKEN") or _read_token(token_file)
    output_root = arguments.output_dir.expanduser().resolve()
    output_root.mkdir(parents=True, exist_ok=True)
    if arguments.install:
        install_runtime(arguments.install_dir.expanduser().resolve())
    server = AgentHTTPServer(
        (arguments.host, arguments.port),
        token,
        output_root,
        arguments.install_dir.expanduser(),
        False,
    )
    print(f"agent server listening on http://{arguments.host}:{arguments.port}", flush=True)
    print(f"token file: {token_file}", flush=True)
    try:
        server.serve_forever()
    finally:
        server.server_close()


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Expose the GIMP MCP image decomposition agent over HTTP.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8080)
    parser.add_argument("--output-dir", type=Path, default=Path(".agent-jobs"))
    parser.add_argument("--install-dir", type=Path, default=default_install_dir())
    parser.add_argument("--token-file", type=Path, default=Path("~/.cache/rob-boss/agent-api-token"))
    parser.add_argument("--install", action="store_true", help="Install or update the pinned GIMP MCP runtime before serving.")
    arguments = parser.parse_args(argv)
    run_server(arguments)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
