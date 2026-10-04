from __future__ import annotations

import argparse
import contextlib
import json
import os
import shutil
import signal
import socket
import subprocess
import time
from pathlib import Path
from typing import Any, Iterator, Sequence

UPSTREAM_URL = "https://github.com/maorcc/gimp-mcp.git"
UPSTREAM_COMMIT = "09bfb2d3e5ca8efdc50c8d0b8c9cdf590ce422c6"
GIMP_PORT = 9877


def default_install_dir() -> Path:
    cache_home = Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache"))
    return cache_home / "rob-boss" / "gimp-mcp"


def install_runtime(install_dir: Path) -> None:
    install_dir = install_dir.expanduser().resolve()
    if not install_dir.exists():
        install_dir.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(
            ["git", "clone", UPSTREAM_URL, str(install_dir)],
            check=True,
        )

    head = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=install_dir,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    if head != UPSTREAM_COMMIT:
        subprocess.run(
            ["git", "fetch", "origin", UPSTREAM_COMMIT],
            cwd=install_dir,
            check=True,
        )
        subprocess.run(
            ["git", "checkout", "--detach", UPSTREAM_COMMIT],
            cwd=install_dir,
            check=True,
        )

    subprocess.run(
        ["uv", "sync", "--python", "3.13", "--frozen", "--no-dev"],
        cwd=install_dir,
        check=True,
    )


def runtime_paths(install_dir: Path) -> dict[str, Path]:
    root = install_dir.parent / "runtime"
    return {
        "root": root,
        "home": root / "home",
        "config": root / "config",
        "cache": root / "cache",
        "data": root / "data",
        "log": root / "gimp.log",
    }


def prepare_gimp_profile(install_dir: Path) -> dict[str, Path]:
    paths = runtime_paths(install_dir)
    for key in ("home", "config", "cache", "data"):
        paths[key].mkdir(parents=True, exist_ok=True)

    plugin_dir = (
        paths["config"]
        / "GIMP"
        / "3.2"
        / "plug-ins"
        / "gimp-mcp-plugin"
    )
    plugin_dir.mkdir(parents=True, exist_ok=True)
    plugin = plugin_dir / "gimp-mcp-plugin.py"
    shutil.copy2(install_dir / "gimp-mcp-plugin.py", plugin)
    plugin.chmod(0o755)
    return paths


def _port_open() -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.2)
        return sock.connect_ex(("127.0.0.1", GIMP_PORT)) == 0


@contextlib.contextmanager
def gimp_service(install_dir: Path, timeout_seconds: float = 30.0) -> Iterator[None]:
    if _port_open():
        raise RuntimeError(
            f"localhost:{GIMP_PORT} is already in use; refusing to connect to an "
            "unverified GIMP MCP process"
        )

    paths = prepare_gimp_profile(install_dir)
    env = os.environ.copy()
    env.update(
        {
            "HOME": str(paths["home"]),
            "XDG_CONFIG_HOME": str(paths["config"]),
            "XDG_CACHE_HOME": str(paths["cache"]),
            "XDG_DATA_HOME": str(paths["data"]),
        }
    )
    batch = (
        "p=Gimp.get_pdb().lookup_procedure('plug-in-mcp-server'); "
        "c=p.create_config(); "
        "c.set_property('run-mode', Gimp.RunMode.NONINTERACTIVE); "
        "p.run(c)"
    )
    command = [
        "xvfb-run",
        "-a",
        "-s",
        "-screen 0 1280x1024x24",
        "/usr/sbin/gimp",
        "-n",
        "-s",
        "-c",
        "--no-shm",
        "--batch-interpreter=python-fu-eval",
        "-b",
        batch,
    ]

    with paths["log"].open("w", encoding="utf-8") as log:
        process = subprocess.Popen(
            command,
            env=env,
            stdout=log,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
        # The startup wait is inside the try: a GIMP that is too slow to open the port must
        # still be killed, or it binds 9877 later and every following request is refused.
        try:
            deadline = time.monotonic() + timeout_seconds
            while not _port_open():
                if process.poll() is not None:
                    raise RuntimeError(
                        f"GIMP exited with status {process.returncode}; see {paths['log']}"
                    )
                if time.monotonic() >= deadline:
                    raise TimeoutError(
                        f"GIMP MCP did not open localhost:{GIMP_PORT}; see {paths['log']}"
                    )
                time.sleep(0.1)
            yield
        finally:
            try:
                os.killpg(process.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                process.wait(timeout=5)


class McpClient:
    def __init__(self, install_dir: Path, timeout_seconds: float = 60.0):
        self.install_dir = install_dir
        self.timeout_seconds = timeout_seconds
        self._next_id = 1
        self._stderr = (install_dir.parent / "runtime" / "mcp.log").open(
            "w", encoding="utf-8"
        )
        self._process = subprocess.Popen(
            [
                str(install_dir / ".venv" / "bin" / "python"),
                str(install_dir / "gimp_mcp_server.py"),
            ],
            cwd=install_dir,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=self._stderr,
            text=True,
            bufsize=1,
        )
        self._initialize()

    def _send(self, message: dict[str, Any]) -> None:
        if self._process.stdin is None:
            raise RuntimeError("MCP stdin is unavailable")
        self._process.stdin.write(json.dumps(message, separators=(",", ":")) + "\n")
        self._process.stdin.flush()

    def _request(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        request_id = self._next_id
        self._next_id += 1
        self._send(
            {
                "jsonrpc": "2.0",
                "id": request_id,
                "method": method,
                "params": params,
            }
        )
        if self._process.stdout is None:
            raise RuntimeError("MCP stdout is unavailable")

        deadline = time.monotonic() + self.timeout_seconds
        while time.monotonic() < deadline:
            if self._process.poll() is not None:
                raise RuntimeError(
                    f"MCP server exited with status {self._process.returncode}"
                )
            remaining = max(0.0, deadline - time.monotonic())
            import select

            readable, _, _ = select.select([self._process.stdout], [], [], remaining)
            if not readable:
                break
            line = self._process.stdout.readline()
            if not line:
                continue
            response = json.loads(line)
            if response.get("id") != request_id:
                continue
            if "error" in response:
                raise RuntimeError(f"MCP {method} failed: {response['error']}")
            return response["result"]
        raise TimeoutError(f"MCP {method} timed out after {self.timeout_seconds}s")

    def _initialize(self) -> None:
        self._request(
            "initialize",
            {
                "protocolVersion": "2025-06-18",
                "capabilities": {},
                "clientInfo": {"name": "rob-boss-spike-c", "version": "1.0"},
            },
        )
        self._send({"jsonrpc": "2.0", "method": "notifications/initialized"})

    def call(self, name: str, arguments: dict[str, Any] | None = None) -> Any:
        result = self._request(
            "tools/call",
            {"name": name, "arguments": arguments or {}},
        )
        if result.get("isError"):
            raise RuntimeError(f"MCP tool {name} failed: {result.get('content')}")
        if result.get("structuredContent") is not None:
            return result["structuredContent"]

        texts = [
            item["text"]
            for item in result.get("content", [])
            if item.get("type") == "text"
        ]
        text = "\n".join(texts)
        if text.startswith("Error:"):
            raise RuntimeError(f"MCP tool {name} failed: {text}")
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            return text

    def close(self) -> None:
        if self._process.stdin is not None:
            self._process.stdin.close()
        self._process.terminate()
        try:
            self._process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self._process.kill()
            self._process.wait(timeout=5)
        self._stderr.close()

    def __enter__(self) -> "McpClient":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Install the pinned GIMP MCP runtime.")
    parser.add_argument("--install-dir", type=Path, default=default_install_dir())
    args = parser.parse_args(argv)
    install_runtime(args.install_dir)
    print(args.install_dir.expanduser().resolve())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
