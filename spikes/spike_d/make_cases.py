"""Spike D: build paintings from a real reference, each with one planted defect.

The reference is reference_source.jpg (a sunset landscape painting). Four steps are
tested: sky, mountains, water, foreground trees/rocks. For step k, every earlier step
is painted perfectly (the reference's own pixels on bare canvas) and later steps are
left bare; the current step's region is the correct painting or carries one defect.
Ground truth lives in cases/truth.json so scoring never depends on filenames.
"""
import json
import shutil
from pathlib import Path

import numpy as np
from PIL import Image, ImageFilter

HERE = Path(__file__).parent
OUT = HERE / "cases"
SIZE = (768, 512)  # W, H
CANVAS = np.array([245, 240, 230], dtype=np.float32)
WATERLINE = 356  # y of the far shore at 512px height
FG_X, FG_Y = 290, 396  # foreground trees/rocks box (x < FG_X, y < FG_Y, below the sky)


def blur(arr, radius):
    img = Image.fromarray(np.clip(arr, 0, 255).astype(np.uint8)).filter(ImageFilter.GaussianBlur(radius))
    return np.asarray(img).astype(np.float32)


def sky_mask(ref) -> np.ndarray:
    """Sky = everything above the first dark/mountain pixel in each column."""
    h = ref.shape[0]
    ys = np.arange(h)[:, None]
    hit = (ref.max(axis=2) < 120) | ((ys >= int(h * 0.47)) & (ref[..., 0] < 150))
    nonsky = np.maximum.accumulate(hit, axis=0)
    grown = Image.fromarray((nonsky * 255).astype(np.uint8)).filter(ImageFilter.MaxFilter(5))
    return ~(np.asarray(grown) > 0)


def region_masks(ref):
    h, w = ref.shape[:2]
    ys, xs = np.mgrid[0:h, 0:w]
    sky = sky_mask(ref)
    # Foreground = dark tree/rock pixels in the left box, closed to fill needle gaps.
    # Everything else below the sky goes to mountains/water by height.
    box = ~sky & (xs < FG_X) & (ys < FG_Y)
    dark = box & (ref.max(axis=2) < 125)
    img = Image.fromarray((dark * 255).astype(np.uint8))
    closed = img.filter(ImageFilter.MaxFilter(15)).filter(ImageFilter.MinFilter(15))
    fg = (np.asarray(closed) > 0) & box
    water = ~sky & ~fg & (ys >= WATERLINE)
    mountains = ~sky & ~fg & (ys < WATERLINE)
    return {"sky": sky, "mountains": mountains, "water": water, "foreground": fg}


STEPS = [
    {"key": "sky", "name": "Block in the sky",
     "mix": [("ultramarine blue", 3), ("cadmium orange", 2), ("titanium white", 1)],
     "brush": "1in flat", "dir": 0, "dark": False,
     "technique": ("broken horizontal strokes with visible brush texture; blue at the top grading "
                   "to orange near the horizon; crisp edge along the mountain ridge and trees"),
     "success": "sky fully covered down to the mountain ridge and around the trees, values match the reference"},
    {"key": "mountains", "name": "Paint the mountains and far shore",
     "mix": [("ultramarine blue", 2), ("alizarin crimson", 1), ("titanium white", 2)],
     "brush": "1in flat", "dir": 0, "dark": False,
     "technique": "broken horizontal strokes, cool blue-violet; darker far treeline along the waterline",
     "success": "mountains and far shore fully covered between the sky and the water, cool violet values"},
    {"key": "water", "name": "Block in the water and sun reflection",
     "mix": [("ultramarine blue", 3), ("cadmium orange", 1), ("titanium white", 1)],
     "brush": "1in flat", "dir": 0, "dark": False,
     "technique": "calm horizontal strokes; orange-yellow glow in the middle, blue toward the edges",
     "success": "water fully covered from the far shore to the bottom edge, horizontal strokes"},
    {"key": "foreground", "name": "Paint the foreground trees and rocks",
     "mix": [("phthalo green", 2), ("burnt umber", 2), ("ivory black", 1)],
     "brush": "#6 round", "dir": 90, "dark": True,
     "technique": "dabbed, mostly vertical strokes for the trees; darkest values of the painting",
     "success": "trees and rock fully covered, very dark values, silhouette matches the reference"},
]


def compose(ref, paint, region) -> np.ndarray:
    out = np.broadcast_to(CANVAS, ref.shape).copy()
    out[region] = paint[region]
    return out


def save(arr, path):
    Image.fromarray(np.clip(arr, 0, 255).astype(np.uint8)).save(path)


def swapped_texture(ref):
    """Keep the colour layout but rotate the brush texture 90 degrees."""
    low = blur(ref, 10)
    rot = np.rot90(ref - low)
    rot = np.stack([np.asarray(Image.fromarray(rot[..., c]).resize(ref.shape[1::-1], Image.BILINEAR))
                    for c in range(3)], axis=2)
    return low + rot * 1.5


def hole_for(region, ys, xs):
    """A patch inside `region` covering roughly a fifth of it."""
    ry, rx = ys[region], xs[region]
    cy, cx = np.median(ry), np.percentile(rx, 70)
    hh, hw = (ry.max() - ry.min()) * 0.3, (rx.max() - rx.min()) * 0.2
    return region & (abs(ys - cy) < hh) & (abs(xs - cx) < hw)


def main():
    OUT.mkdir(exist_ok=True)
    for old in OUT.iterdir():  # clear contents only; OneDrive can refuse to delete the folder itself
        shutil.rmtree(old, ignore_errors=True) if old.is_dir() else old.unlink()
    ref = np.asarray(Image.open(HERE / "reference_source.jpg").convert("RGB").resize(SIZE, Image.LANCZOS)).astype(np.float32)
    h, w = ref.shape[:2]
    ys, xs = np.mgrid[0:h, 0:w]
    masks = region_masks(ref)
    save(ref, OUT / "reference.png")

    truth_steps = []
    done = np.zeros((h, w), bool)
    for i, s in enumerate(STEPS, 1):
        region = masks[s["key"]]
        d = OUT / f"step{i}"
        d.mkdir(exist_ok=True)
        avg = [int(v) for v in ref[region].mean(axis=0)]
        step = {
            "index": i, "name": s["name"], "target_rgb": avg,
            "mix": [{"pigment": p, "parts": n} for p, n in s["mix"]],
            "brush": s["brush"], "technique": s["technique"], "stroke_dir_deg": s["dir"],
            "success": s["success"] + ". Earlier steps are already painted; later steps' areas are "
                                      "intentionally still bare",
        }
        cases = {}

        def add(cid, painted_region_paint, category, note, region_painted=region):
            img = compose(ref, painted_region_paint, done | region_painted)
            if cid == "overblended":
                img = blur(img, 5)
            save(img, d / f"{cid}.png")
            cases[cid] = {"expected_category": category, "note": note}

        # Earlier steps are untouched ref pixels, so each case only alters the current region.
        def variant(arr):
            out = ref.copy()
            out[region] = arr[region]
            return out

        if s["dark"]:
            wrong = variant(ref * 0.7 + 255 * 0.3)  # too light for the darkest step, still clearly painted
            wrong_note = "paint is far too light; should be darker"
        else:
            wrong = variant(ref * 0.55)
            wrong_note = "paint is far too dark; should be lighter"
        add("correct", ref, "none", "step fully painted exactly as the reference")
        add("wrong_value", wrong, "value", wrong_note)
        add("missed_region", ref, "coverage", "bare-canvas patch inside the step's region",
            region_painted=region & ~hole_for(region, ys, xs))
        add("wrong_stroke_dir", variant(swapped_texture(ref)), "stroke_direction",
            f"brush texture rotated 90 degrees from the step's {s['dir']} deg")
        add("overblended", variant(blur(ref, 6)), "blending", "smooth blurry paint, no stroke texture")
        cut = np.percentile(ys[region], 45)
        add("incomplete", ref, "coverage", "right colors but only the top ~45% of the region is painted",
            region_painted=region & (ys <= cut))
        truth_steps.append({"step": step, "dir": f"step{i}", "cases": cases})
        done = done | region

    (OUT / "truth.json").write_text(json.dumps({"steps": truth_steps}, indent=2))
    print(f"wrote reference + {len(STEPS)} steps x 6 cases to {OUT}")
    for k, m in masks.items():
        print(f"  {k}: {m.mean() * 100:.1f}% of canvas")


if __name__ == "__main__":
    main()
