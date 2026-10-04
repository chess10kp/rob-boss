"""LightGuide backend: draw on LightGuide's projector canvas and read its shared camera.

Everything goes through one long-lived `LGCLI.exe shell` session (WCF to the running
LightGuide app), so LightGuide keeps running and keeps owning the camera:

- a frame is a `camera` command; LightGuide's camera buffer shows a projection change
  ~0.3 s after it is displayed. The first frame starts the camera (~13 s).
- an overlay is a full-canvas PNG written into LightGuide's VDFGraphics folder (it
  refuses files anywhere else) and shown with `graphic`. X/Y are the graphic's
  *centre*; at the canvas size it lands pixel-exact.
- LightGuide lets an idle camera go after ~10 s, and restarting it costs ~11.5 s, so
  a keep-alive thread pulls a frame (0.05 s) whenever the session has been idle.
"""

from __future__ import annotations

import queue
import subprocess
import tempfile
import threading
import time
from pathlib import Path

import cv2
import numpy as np

DEFAULT_LGCLI = r"C:\Users\LGS USER\Downloads\SDK\LGCLI\LGCLI.exe"
GRAPHICS_DIR = Path(r"C:\Program Files\OPS Solutions\Light Guide Systems\VDFGraphics")
SUBDIR = "PalettePilot"
KEEP_FILES = 3          # overlay PNGs kept in VDFGraphics/PalettePilot; older ones are deleted
KEEPALIVE_S = 3.0       # idle time after which the keep-alive thread pulls a camera frame


class LightGuideError(RuntimeError):
    pass


class LightGuide:
    def __init__(self, lgcli=DEFAULT_LGCLI, camera="Camera1", canvas=1, size=(1920, 1280), wcf_timeout_s=None):
        self.camera, self.canvas = camera, canvas
        self.proj_size = tuple(size)
        self.dir = GRAPHICS_DIR / SUBDIR
        self.dir.mkdir(exist_ok=True)
        self._tmp = Path(tempfile.mkdtemp(prefix="palettepilot_"))
        self._shown: list[Path] = []
        self._n = 0
        self._camera_started = False

        opts = ["--timeout", str(wcf_timeout_s)] if wcf_timeout_s else []
        self._proc = subprocess.Popen([lgcli, *opts, "shell"], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                      stderr=subprocess.STDOUT, text=True, bufsize=1)
        self._lines: queue.Queue[str] = queue.Queue()
        self._lock = threading.Lock()                       # one command at a time on the shell
        self._last_cmd = time.monotonic()
        self._stop = threading.Event()
        threading.Thread(target=self._pump, daemon=True).start()
        self._send("status", "Connected", timeout=30)
        threading.Thread(target=self._keepalive, daemon=True).start()

    def _pump(self):
        for line in self._proc.stdout:
            self._lines.put(line.rstrip())

    def _keepalive(self):
        """Keep LightGuide's camera running so frames stay fast (see module docstring)."""
        out = (self._tmp / "keepalive.png").as_posix()
        while not self._stop.wait(0.5):
            if self._camera_started and time.monotonic() - self._last_cmd > KEEPALIVE_S:
                try:
                    self._send(f'camera {self.camera} --out "{out}"', "Saved", timeout=30)
                except LightGuideError:
                    pass                                    # the next real command reports problems

    def _send(self, cmd, ok, timeout=15.0):
        """Run one shell command; return its result line once it contains `ok`."""
        with self._lock:
            try:
                return self._send_locked(cmd, ok, timeout)
            finally:
                self._last_cmd = time.monotonic()

    def _send_locked(self, cmd, ok, timeout):
        if self._proc.poll() is not None:
            raise LightGuideError("LGCLI shell has exited")
        while not self._lines.empty():                     # drop anything left from an earlier command
            self._lines.get_nowait()
        self._proc.stdin.write(cmd + "\n")
        self._proc.stdin.flush()
        end, seen = time.monotonic() + timeout, []
        while time.monotonic() < end:
            try:
                line = self._lines.get(timeout=0.2)
            except queue.Empty:
                continue
            seen.append(line)
            if ok in line:
                return line
            if "error" in line.lower() or "not connected" in line.lower():
                raise LightGuideError(f"{cmd!r}: {line}")
        raise LightGuideError(f"{cmd!r} timed out after {timeout:.0f}s; output: {seen[-3:]}")

    def show(self, img_bgr, settle_s=0.0):
        """Show a full-canvas BGR image (proj_size) on the projector."""
        w, h = self.proj_size
        if img_bgr.shape[:2] != (h, w):
            raise ValueError(f"overlay is {img_bgr.shape[1]}x{img_bgr.shape[0]}, canvas is {w}x{h}")
        # a fresh name each time, so LightGuide can't serve a cached copy of an older overlay
        self._n += 1
        path = self.dir / f"overlay_{self._n:06d}.png"
        cv2.imwrite(str(path), img_bgr)
        self._send(f'graphic "{SUBDIR}/{path.name}" --x {w // 2} --y {h // 2} --width {w} --height {h} '
                   f"--canvas {self.canvas} --replace", "Showing")
        self._shown.append(path)
        while len(self._shown) > KEEP_FILES:
            self._shown.pop(0).unlink(missing_ok=True)
        if settle_s:
            time.sleep(settle_s)

    def clear(self):
        self._send("clear", "Removed")

    def frame(self):
        out = self._tmp / "frame.png"
        timeout = 15.0 if self._camera_started else 60.0      # first frame starts the camera
        self._send(f'camera {self.camera} --out "{out.as_posix()}"', "Saved", timeout=timeout)
        self._camera_started = True
        img = cv2.imread(str(out))
        if img is None:
            raise LightGuideError(f"camera frame unreadable: {out}")
        return img

    def close(self):
        self._stop.set()
        try:
            self.clear()
            self._proc.stdin.write("exit\n")
            self._proc.stdin.flush()
            self._proc.wait(timeout=5)
        except (LightGuideError, OSError, subprocess.TimeoutExpired):
            self._proc.kill()
        for p in self._shown:
            p.unlink(missing_ok=True)
