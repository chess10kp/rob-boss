"""Track 1: the only code that converts between the physical workspace and canvas space.

    rig = Rig()                       # backends from config/rig_settings.json
    rig.detect_canvas()               # on demand, not per frame; saved for reuse
    rig.project_overlay(mask, style)  # mask in canvas space -> lands on the canvas
    img = rig.capture_canvas()        # flash, capture, restore overlay, rectify
    rig.close()

Projector and camera are chosen separately. Default: LightGuide draws (it keeps
running and owns the projector), and the camera is read directly - LightGuide's own
camera restarts every ~30 s for ~11.5 s, the direct reader doesn't and is faster.

Capture uses flash-before-capture, not blank (Spike B): the projector shows flat grey
for a moment so the camera reads colour under a known, repeatable light, then the
overlay is put back.
"""

from __future__ import annotations

import json
import time

import cv2
import numpy as np

from config import rig as config_rig
from . import colour, geometry, overlay

QUAD_PATH = config_rig.CONFIG_DIR / "canvas_quad.json"
DEFAULTS = {
    "projector": "lightguide",        # lightguide | direct
    "camera_source": "direct",        # direct | lightguide
    "lgcli": None,                    # path to LGCLI.exe (lightguide.DEFAULT_LGCLI if unset)
    "lg_camera": "Camera1",
    "lg_canvas": 1,
    "flash_level": 190,               # grey used to light captures (Spike B)
    "flash_settle_s": 0.4,            # change -> camera: ~0.15 s direct, ~0.3 s via LightGuide
    "canvas_mm": None,                # [w, h] of the canvas, e.g. letter paper; checks detected shape
    "colour_correct": True,           # pre-correct overlays with config/projector_profile.json, if present
}


class Rig:
    def __init__(self, projector=None, camera_source=None):
        s = {**DEFAULTS, **config_rig.load_settings()}
        self.settings = s
        self.projector_kind = projector or s["projector"]
        self.camera_kind = camera_source or s["camera_source"]
        for kind in (self.projector_kind, self.camera_kind):
            if kind not in ("lightguide", "direct"):
                raise ValueError(f"unknown backend {kind!r}")

        lg = None
        if "lightguide" in (self.projector_kind, self.camera_kind):
            from .lightguide import LightGuide, DEFAULT_LGCLI
            lg = LightGuide(s["lgcli"] or DEFAULT_LGCLI, s["lg_camera"], s["lg_canvas"])
        if self.projector_kind == "lightguide":
            self.projector = lg
        else:
            from .direct import DirectProjector
            self.projector = DirectProjector(s.get("display"))
        if self.camera_kind == "lightguide":
            self.camera = lg
        else:
            from .direct import DirectCamera
            res = config_rig.parse_res(s["cam_res"]) if s.get("cam_res") else None
            self.camera = DirectCamera(s.get("camera", 0), res)
        self.proj_size = self.projector.proj_size
        self.profile = None
        if s["colour_correct"] and colour.PROFILE_PATH.exists():
            self.profile = colour.Profile.load()
        self._black = np.zeros((self.proj_size[1], self.proj_size[0], 3), np.uint8)
        self._overlay = self._black
        self._cam_to_proj = None
        self.cam_size = None
        self.quad_cam = None
        self.quad_proj = None

    # ---------------- camera + calibration ----------------

    def frame(self):
        """Raw camera frame of whatever is projected right now."""
        f = self.camera.frame()
        size = (f.shape[1], f.shape[0])
        if size != self.cam_size:
            self.cam_size = size
            self._cam_to_proj = self._load_homography(size)
            self._load_quad()
        return f

    @staticmethod
    def _load_homography(cam_size):
        path = config_rig.HOMOGRAPHY_PATH
        if not path.exists():
            raise RuntimeError(f"{path.name} not found - run config/checkerboard.py first (Spike A).")
        data = np.load(path)
        return geometry.scale_homography(data["H"], tuple(data["cam_size"]), cam_size)

    @property
    def cam_to_proj(self):
        if self._cam_to_proj is None:
            self.frame()
        return self._cam_to_proj

    def flash_capture(self):
        """Light the scene with flat grey, grab a frame, put the overlay back."""
        level = int(self.settings["flash_level"])
        self.projector.show(np.full_like(self._black, level), self.settings["flash_settle_s"])
        try:
            return self.frame()
        finally:
            self._show(self._overlay)

    # ---------------- canvas quad ----------------

    def _set_quad(self, quad_cam):
        self.quad_cam = geometry.order_quad(quad_cam)
        self.quad_proj = geometry.transform(self.quad_cam, self.cam_to_proj)

    def _load_quad(self):
        """Reuse a saved quad if it was found at this camera resolution (scaled if the
        resolution differs only by a factor, e.g. LightGuide's half-size frames)."""
        self.quad_cam = self.quad_proj = None
        if not QUAD_PATH.exists():
            return
        data = json.loads(QUAD_PATH.read_text())
        sw, sh = data["cam_size"]
        w, h = self.cam_size
        if abs(sw / sh - w / h) > 0.01:
            return
        self._set_quad(np.float32(data["quad_cam"]) * np.float32([w / sw, h / sh]))

    def detect_canvas(self, manual=False):
        """Find the canvas (on demand, not per frame) and save it. Returns the camera-px quad."""
        frame = self.flash_capture()
        mm = self.settings["canvas_mm"]
        aspect = max(mm) / min(mm) if mm else None
        quad = None if manual else geometry.detect_canvas_quad(frame, self.cam_to_proj, self.proj_size, aspect)
        if quad is None:
            if not manual:
                print("Canvas not found automatically - click its corners TL, TR, BR, BL.")
            quad = click_quad(frame)
        self._set_quad(quad)
        QUAD_PATH.write_text(json.dumps({"quad_cam": self.quad_cam.tolist(),
                                         "cam_size": list(self.cam_size)}, indent=2))
        return self.quad_cam

    def _require_quad(self):
        if self.quad_cam is None:
            if self.cam_size is None:
                self.frame()                       # loads a saved quad, if any
            if self.quad_cam is None:
                raise RuntimeError("no canvas yet - call detect_canvas()")

    def canvas_size(self, long_side=None):
        """Pixel size of a canvas-space image; default = the canvas's footprint in projector px."""
        self._require_quad()
        return geometry.canvas_pixels(self.quad_proj, long_side)

    # ---------------- the Track 1 interface ----------------

    def capture_canvas(self, long_side=1024):
        """Flash-lit capture, rectified so the canvas fills the image (canvas space)."""
        self._require_quad()
        frame = self.flash_capture()
        return geometry.rectify(frame, self.quad_cam, self.canvas_size(long_side))

    def project_overlay(self, mask, style=None):
        """Render a canvas-space mask with `style` (see overlay.DEFAULT_STYLE) onto the canvas."""
        self._overlay = self._overlay_image(mask, style)
        self._show(self._overlay)

    def flash_correction(self, mask, times=3, on_s=0.35, off_s=0.25, style=None):
        """Blink a region loudly (the correction flash), then restore the current overlay."""
        loud = self._overlay_image(mask, {**overlay.CORRECTION_STYLE, **(style or {})})
        for _ in range(times):
            self._show(loud)
            time.sleep(on_s)
            self._show(self._overlay)
            time.sleep(off_s)

    def clear(self):
        self._overlay = self._black
        self.projector.clear()

    def close(self):
        self.camera.close()
        if self.projector is not self.camera:
            self.projector.close()

    # ---------------- internals ----------------

    def _overlay_image(self, mask, style):
        self._require_quad()
        img = overlay.render(mask, style, self.canvas_size())
        if self.profile is not None:                 # the projector is short of red (track1/colour.py)
            img = self.profile.correct(img)
        return geometry.canvas_to_projector(img, self.quad_proj, self.proj_size)

    def _show(self, img):
        if img is self._black:
            self.projector.clear()
        else:
            self.projector.show(img)


def click_quad(frame, title="click canvas corners TL, TR, BR, BL - ENTER accepts, U undoes"):
    """Manual fallback: click the four canvas corners on a preview of the frame."""
    scale = min(1.0, 1280 / frame.shape[1])
    base = cv2.resize(frame, None, fx=scale, fy=scale)
    pts = []

    def on_mouse(event, x, y, *_):
        if event == cv2.EVENT_LBUTTONDOWN and len(pts) < 4:
            pts.append((x / scale, y / scale))

    cv2.namedWindow(title)
    cv2.setMouseCallback(title, on_mouse)
    while True:
        disp = base.copy()
        for i, (x, y) in enumerate(pts):
            cv2.circle(disp, (int(x * scale), int(y * scale)), 6, (0, 255, 0), 2)
            cv2.putText(disp, "TL TR BR BL".split()[i], (int(x * scale) + 8, int(y * scale) - 8),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 255, 0), 2)
        cv2.imshow(title, disp)
        key = cv2.waitKey(20) & 0xFF
        if key == 27:
            cv2.destroyWindow(title)
            raise SystemExit("aborted")
        if key in (ord("u"), ord("U")) and pts:
            pts.pop()
        if key in (13, 10) and len(pts) == 4:
            break
    cv2.destroyWindow(title)
    return np.float32(pts)
