"""Local, no-AI measurements of a rectified canvas against a step's mask.

Answers the questions CV answers better than a model: how much of the step's region is
painted, and is the paint lighter or darker than the reference. Everything is in canvas
space at the mask's resolution. Inputs are RGB uint8 arrays.

Limits: paint that is nearly the colour of bare canvas (white on off-white) cannot be seen
as paint; such pixels are treated as "not required" when the reference there is also near
bare canvas. Thresholds are rig-dependent and live in `WatchConfig` (watcher.py).
"""
from __future__ import annotations

from dataclasses import dataclass

import cv2
import numpy as np

DEFAULT_BARE_RGB = (245, 240, 230)


@dataclass
class Measurement:
    checkable: bool            # False if the step has (almost) nothing visible to paint
    coverage: float            # painted fraction of the pixels that need paint (0..1)
    delta_l: float | None      # mean L*(canvas) - L*(reference) over painted pixels; <0 = too dark
    delta_e: float | None      # colour distance between mean painted colour and reference mean
    missing: np.ndarray        # bool HxW at mask size: needs paint but is still bare
    painted_px: int


def to_lab(rgb: np.ndarray) -> np.ndarray:
    return cv2.cvtColor(rgb.astype(np.float32) / 255.0, cv2.COLOR_RGB2LAB)


def bare_lab(bare_rgb=DEFAULT_BARE_RGB) -> np.ndarray:
    return to_lab(np.array([[bare_rgb]], np.uint8))[0, 0]


def estimate_bare_rgb(canvas_rgb: np.ndarray) -> tuple[int, int, int]:
    """Calibrate from an (empty) canvas capture: per-channel median."""
    return tuple(int(v) for v in np.median(canvas_rgb.reshape(-1, 3), axis=0))


def measure(canvas_rgb: np.ndarray, ref_rgb: np.ndarray, mask: np.ndarray, *,
            bare_rgb=DEFAULT_BARE_RGB, paint_de: float = 12.0, erode_px: int = 5,
            min_needed_px: int = 200, min_painted_px: int = 50) -> Measurement:
    h, w = mask.shape[:2]
    canvas = cv2.resize(canvas_rgb, (w, h), interpolation=cv2.INTER_AREA)
    ref = cv2.resize(ref_rgb, (w, h), interpolation=cv2.INTER_AREA)
    region = mask > 127
    if erode_px > 1:  # ignore a thin border so small registration error doesn't read as bare canvas
        region = cv2.erode(region.astype(np.uint8), np.ones((erode_px, erode_px), np.uint8)).astype(bool)

    lab_c, lab_r, lab_b = to_lab(canvas), to_lab(ref), bare_lab(bare_rgb)
    painted_any = np.linalg.norm(lab_c - lab_b, axis=2) > paint_de
    needs_paint = region & (np.linalg.norm(lab_r - lab_b, axis=2) > paint_de)

    if needs_paint.sum() < min_needed_px:
        return Measurement(False, 1.0, None, None, np.zeros_like(region), 0)

    painted = needs_paint & painted_any
    coverage = float(painted.sum() / needs_paint.sum())
    delta_l = delta_e = None
    if painted.sum() >= min_painted_px:
        mean_c, mean_r = lab_c[painted].mean(axis=0), lab_r[painted].mean(axis=0)
        delta_l = float(mean_c[0] - mean_r[0])
        delta_e = float(np.linalg.norm(mean_c - mean_r))
    return Measurement(True, coverage, delta_l, delta_e, needs_paint & ~painted_any, int(painted.sum()))
