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
    mean_l_canvas: float | None = None  # mean L* of the painted pixels (0-100)
    mean_l_ref: float | None = None     # mean L* of the reference at those same pixels
    value_off: np.ndarray | None = None  # bool HxW: painted areas whose lightness is off locally


def to_lab(rgb: np.ndarray) -> np.ndarray:
    return cv2.cvtColor(rgb.astype(np.float32) / 255.0, cv2.COLOR_RGB2LAB)


def bare_lab(bare_rgb=DEFAULT_BARE_RGB) -> np.ndarray:
    return to_lab(np.array([[bare_rgb]], np.uint8))[0, 0]


def estimate_bare_rgb(canvas_rgb: np.ndarray) -> tuple[int, int, int]:
    """Calibrate from an (empty) canvas capture: per-channel median."""
    return tuple(int(v) for v in np.median(canvas_rgb.reshape(-1, 3), axis=0))


def measure(canvas_rgb: np.ndarray, ref_rgb: np.ndarray, mask: np.ndarray, *,
            bare_rgb=DEFAULT_BARE_RGB, paint_de: float = 12.0, erode_px: int = 5,
            min_needed_px: int = 200, min_painted_px: int = 50,
            value_dl: float = 12.0, blur_px: int = 25) -> Measurement:
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
    delta_l = delta_e = mean_lc = mean_lr = value_off = None
    if painted.sum() >= min_painted_px:
        mean_c, mean_r = lab_c[painted].mean(axis=0), lab_r[painted].mean(axis=0)
        delta_l = float(mean_c[0] - mean_r[0])
        delta_e = float(np.linalg.norm(mean_c - mean_r))
        mean_lc, mean_lr = float(mean_c[0]), float(mean_r[0])
        value_off = _local_value_off(lab_c[..., 0] - lab_r[..., 0], painted, value_dl, blur_px)
    return Measurement(True, coverage, delta_l, delta_e, needs_paint & ~painted_any, int(painted.sum()),
                       mean_lc, mean_lr, value_off)


def _local_value_off(diff_l: np.ndarray, painted: np.ndarray, threshold: float, blur_px: int) -> np.ndarray:
    """Where is the lightness error large locally? Blur over painted pixels only, so brush
    texture doesn't speckle the map and bare canvas doesn't bleed into it."""
    k = blur_px | 1
    weight = cv2.GaussianBlur(painted.astype(np.float32), (k, k), 0)
    smooth = cv2.GaussianBlur(diff_l * painted, (k, k), 0) / np.maximum(weight, 1e-3)
    return painted & (weight > 0.2) & (np.abs(smooth) > threshold)


@dataclass
class Occlusion:
    skin_frac: float            # fraction of the canvas that looks like skin where the reference does not
    outside_change_frac: float  # fraction of the canvas changed outside this step's region since `last`


def _skin_like(rgb: np.ndarray) -> np.ndarray:
    ycrcb = cv2.cvtColor(rgb, cv2.COLOR_RGB2YCrCb)
    cr, cb = ycrcb[..., 1], ycrcb[..., 2]
    return (cr >= 135) & (cr <= 173) & (cb >= 77) & (cb <= 127)


def occlusion(canvas_rgb: np.ndarray, ref_rgb: np.ndarray, mask: np.ndarray,
              last_rgb: np.ndarray | None = None, *, change_de: float = 25.0,
              margin_px: int = 15) -> Occlusion:
    """Is something other than paint (a hand, brush, palette) in the capture?

    Two signals. Skin-coloured pixels that the reference does not have there (so orange sky
    is not mistaken for a hand). And change outside the current step's region compared with
    the last accepted capture: earlier steps are finished and later ones are bare, so
    nothing legitimate should change there. A hand lying over a skin-coloured part of the
    reference is not detectable by colour.
    """
    h, w = mask.shape[:2]
    canvas = cv2.resize(canvas_rgb, (w, h), interpolation=cv2.INTER_AREA)
    ref = cv2.resize(ref_rgb, (w, h), interpolation=cv2.INTER_AREA)
    bad = (_skin_like(canvas) & ~_skin_like(ref)).astype(np.uint8)
    skin_frac = float(cv2.erode(bad, np.ones((3, 3), np.uint8)).mean())

    outside_frac = 0.0
    if last_rgb is not None:
        last = cv2.resize(last_rgb, (w, h), interpolation=cv2.INTER_AREA)
        grown = cv2.dilate((mask > 127).astype(np.uint8), np.ones((margin_px, margin_px), np.uint8)).astype(bool)
        changed = np.linalg.norm(to_lab(canvas) - to_lab(last), axis=2) > change_de
        outside_frac = float((changed & ~grown).mean())
    return Occlusion(skin_frac, outside_frac)
