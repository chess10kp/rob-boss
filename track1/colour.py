"""Projector colour correction.

The Sprout projector (as seen by the overhead camera) is short of red: a projected
ColorChecker loses ~21 levels of red on average, whites arrive cyan (#F3F3F2 -> #BAFCFE),
blues come out 25-50% more saturated and oranges drift towards green.

Model, in linear light: paper = M @ sent + b, fitted by least squares from pairs of
(colour sent, colour the camera read). Correction inverts it: to make the paper read
`want`, send M^-1 (k * want - b). The projector's red is already at full power for
white, so a correct white can only be reached by dimming: k < 1 is the largest scale at
which white is reachable. Corrected projections are dimmer - that is the price.

    profile = Profile.fit(sent_rgb, camera_rgb)     # (N, 3) sRGB 0-255 each
    profile.save(PROFILE_PATH); profile = Profile.load(PROFILE_PATH)
    corrected_bgr = profile.correct(bgr_image)
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from config import rig as config_rig

PROFILE_PATH = config_rig.CONFIG_DIR / "projector_profile.json"


def to_linear(srgb):
    c = np.asarray(srgb, np.float64) / 255.0
    return np.where(c <= 0.04045, c / 12.92, ((c + 0.055) / 1.055) ** 2.4)


def to_srgb(linear):
    c = np.clip(linear, 0.0, 1.0)
    return np.where(c <= 0.0031308, c * 12.92, 1.055 * c ** (1 / 2.4) - 0.055) * 255.0


@dataclass
class Profile:
    M: np.ndarray          # 3x3, linear sent -> linear paper (RGB order)
    b: np.ndarray          # 3, linear offset (room light + projector black)
    k: float               # brightness scale that keeps white reachable

    @classmethod
    def fit(cls, sent_rgb, camera_rgb, white=(243, 243, 242)):
        x = np.column_stack([to_linear(sent_rgb), np.ones(len(sent_rgb))])
        sol, *_ = np.linalg.lstsq(x, to_linear(camera_rgb), rcond=None)
        M, b = sol[:3].T, sol[3]
        p = cls(M, b, 1.0)
        # largest k for which the drive needed for a correct white stays within 0..1
        need = np.linalg.solve(M, to_linear(white) - b)
        p.k = float(min(1.0, 1.0 / max(need.max(), 1e-6)))
        return p

    def predict(self, sent_rgb):
        """sRGB the camera should read for this sent sRGB (model check)."""
        return to_srgb(to_linear(sent_rgb) @ self.M.T + self.b)

    def drive(self, want_rgb):
        """sRGB to send so the paper reads k * want (in linear light)."""
        lin = (self.k * to_linear(want_rgb) - self.b) @ np.linalg.inv(self.M).T
        return to_srgb(lin)

    def correct(self, img_bgr):
        """Pre-correct a BGR uint8 image. Pure black stays black (it emits nothing to fix)."""
        flat = img_bgr.reshape(-1, 3)[:, ::-1]
        out = self.drive(flat)
        out[flat.max(1) == 0] = 0
        return np.round(out[:, ::-1]).astype(np.uint8).reshape(img_bgr.shape)

    def save(self, path: Path = PROFILE_PATH):
        path.write_text(json.dumps({"M": self.M.tolist(), "b": self.b.tolist(), "k": self.k}, indent=2))

    @classmethod
    def load(cls, path: Path = PROFILE_PATH):
        d = json.loads(Path(path).read_text())
        return cls(np.array(d["M"]), np.array(d["b"]), float(d["k"]))
