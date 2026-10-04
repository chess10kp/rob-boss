"""Direct backends: our own fullscreen projector window, and the DirectShow camera.

DirectCamera is the default camera (rig_settings "camera_source": "direct"). A
background thread reads every frame and keeps only the newest, so a capture sees a
projection change ~0.15 s after it is shown - no 2 s buffer drain (config/rig.grab_frame),
and none of the LightGuide camera's 30 s restarts. LightGuide's own camera view must
be off, or it holds the camera.

DirectProjector is the fallback for when LightGuide isn't running.
"""

from __future__ import annotations

import threading
import time

from config import rig


class DirectProjector:
    def __init__(self, display=None):
        self._proj = rig.Projector(rig.pick_display(display))
        self.proj_size = (self._proj.w, self._proj.h)

    def show(self, img_bgr, settle_s=0.0):
        self._proj.show(img_bgr, max(1, int(settle_s * 1000)))

    def clear(self):
        self._proj.blank()

    def close(self):
        self._proj.blank()


class DirectCamera:
    def __init__(self, index=0, resolution=None, first_frame_timeout_s=10.0):
        self._cap = rig.open_camera(index, resolution)
        self._frame, self._stamp = None, 0.0
        self._cond = threading.Condition()
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._read, daemon=True)
        self._thread.start()
        self.frame(timeout_s=first_frame_timeout_s)

    def _read(self):
        while not self._stop.is_set():
            ok, f = self._cap.read()
            if ok:
                with self._cond:
                    self._frame, self._stamp = f, time.monotonic()
                    self._cond.notify_all()

    def frame(self, newer_than=0.0, timeout_s=5.0):
        """Newest frame, waiting for one read after `newer_than` (time.monotonic())."""
        with self._cond:
            if not self._cond.wait_for(lambda: self._frame is not None and self._stamp > newer_than,
                                       timeout_s):
                raise RuntimeError("camera stopped delivering frames - is LightGuide's camera view on?")
            return self._frame.copy()

    def close(self):
        self._stop.set()
        self._thread.join(timeout=2)
        self._cap.release()
