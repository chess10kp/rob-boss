"""Client for the remote Track 3 API (GIMP decomposition, see REMOTE_API.md and server.py).

Sends a reference image to POST /process and downloads the resulting scene:

    scenes/<job_id>/
        public.json          the API response (ordered steps)
        manifest.json        full manifest
        posterized.png, contact-sheet.png
        layers/*.png         one 8-bit mask per step (255 = region)

Usage:
    uv run python -m track3.remote fixtures/spike_c/bobross-sunset.jpg
    uv run python -m track3.remote IMAGE --url https://<tunnel>.trycloudflare.com

The base URL is a Cloudflare Quick Tunnel and changes when the tunnel restarts. It comes
from --url, else ROB_BOSS_API_URL, else the BASE_URL line in REMOTE_API.md (kept current
by whoever restarts the tunnel). The bearer token is read from
~/.cache/rob-boss/agent-api-token (or AGENT_API_TOKEN).
"""

from __future__ import annotations

import argparse
import json
import mimetypes
import os
import re
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TOKEN_FILE = Path("~/.cache/rob-boss/agent-api-token").expanduser()
SCENES_DIR = ROOT / "scenes"


def documented_url() -> str | None:
    """BASE_URL from REMOTE_API.md."""
    doc = ROOT / "REMOTE_API.md"
    m = re.search(r'BASE_URL="(https://[^"]+)"', doc.read_text(encoding="utf-8")) if doc.exists() else None
    return m.group(1) if m else None


def read_token() -> str:
    token = os.environ.get("AGENT_API_TOKEN") or (
        TOKEN_FILE.read_text(encoding="utf-8").strip() if TOKEN_FILE.exists() else "")
    if not token:
        raise SystemExit(f"no API token: put it in {TOKEN_FILE} or set AGENT_API_TOKEN")
    return token


class Remote:
    def __init__(self, base_url: str | None = None, token: str | None = None, timeout_s: float = 600):
        base = base_url or os.environ.get("ROB_BOSS_API_URL") or documented_url()
        if not base:
            raise SystemExit("no API URL: pass --url, set ROB_BOSS_API_URL, or put BASE_URL in REMOTE_API.md")
        self.base = base.rstrip("/")
        self.token = token or read_token()
        self.timeout_s = timeout_s

    def _request(self, path: str, data: bytes | None = None, headers: dict | None = None) -> bytes:
        req = urllib.request.Request(self.base + path, data=data, method="POST" if data is not None else "GET",
                                     headers={"Authorization": f"Bearer {self.token}",
                                              "User-Agent": "rob-boss-track3-client", **(headers or {})})
        try:
            with urllib.request.urlopen(req, timeout=self.timeout_s) as resp:
                return resp.read()
        except urllib.error.HTTPError as e:
            raise RuntimeError(f"{path}: HTTP {e.code} {e.read().decode(errors='replace')[:300]}") from None

    def health(self) -> dict:
        return json.loads(self._request("/healthz"))

    def process(self, image: Path) -> dict:
        """Run the decomposition; returns the public manifest (job_id, steps, artifacts)."""
        ctype = mimetypes.guess_type(image.name)[0] or "application/octet-stream"
        return json.loads(self._request("/process", image.read_bytes(),
                                        {"Content-Type": ctype, "X-Filename": image.name}))

    def download_scene(self, public: dict, out_root: Path = SCENES_DIR) -> Path:
        """Fetch every step mask and artifact into out_root/<job_id>/ (server layout)."""
        job = public["job_id"]
        prefix = f"/jobs/{job}/scene/"
        scene = out_root / job
        paths = [s["path"] for s in public["steps"]] + list(public["artifacts"].values())
        for p in paths:
            if not p.startswith(prefix):
                raise RuntimeError(f"unexpected artifact path {p}")
            dest = scene / p[len(prefix):]
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(self._request(p))
        (scene / "public.json").write_text(json.dumps(public, indent=2))
        return scene


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("image", type=Path)
    ap.add_argument("--url", help="API base URL (default: ROB_BOSS_API_URL, else REMOTE_API.md's BASE_URL)")
    args = ap.parse_args()

    remote = Remote(args.url)
    print(f"{remote.base}: {remote.health()}")
    public = remote.process(args.image)
    print(f"job {public['job_id']}  engine {public['engine']}  {public['width']}x{public['height']}")
    for i, s in enumerate(public["steps"], 1):
        extra = {k: v for k, v in s.items() if k != "path"}
        print(f"  step {i}: {Path(s['path']).name}  {extra}")
    scene = remote.download_scene(public)
    print(f"saved to {scene}")


if __name__ == "__main__":
    main()
