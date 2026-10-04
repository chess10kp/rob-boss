"""Stand-in for Track 3 until Spike C ships: five value-band masks from luminance quantiles.

Honors the same file contract as track3.validate: scene_dir/layers/0N_*.png, 8-bit
grayscale, values 0/255, a clean partition of the canvas.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
from PIL import Image, ImageFilter

from track3.validate import EXPECTED_MASKS


def make_value_masks(ref_path: Path, scene_dir: Path, size: tuple[int, int] = (768, 512)) -> list[Path]:
    ref = Image.open(ref_path).convert("L").resize(size, Image.LANCZOS)
    lum = np.asarray(ref.filter(ImageFilter.GaussianBlur(2)))
    cuts = np.quantile(lum, [0.2, 0.4, 0.6, 0.8])
    labels = np.digitize(lum, cuts).astype(np.uint8)  # 0..4, a partition by construction
    # Despeckle: mode filter keeps the partition (every pixel still has exactly one label).
    labels = np.asarray(Image.fromarray(labels).filter(ImageFilter.ModeFilter(9)))

    layers = scene_dir / "layers"
    layers.mkdir(parents=True, exist_ok=True)
    paths = []
    for i, name in enumerate(EXPECTED_MASKS):
        path = layers / name
        Image.fromarray(np.where(labels == i, 255, 0).astype(np.uint8)).save(path)
        paths.append(path)
    return paths
