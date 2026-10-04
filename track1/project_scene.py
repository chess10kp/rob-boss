"""
Project a Track 3 scene onto the canvas, step by step (Gate 3, join 1: Track 3 -> Track 1).

Reads either scene format:
  - a layer stack from track3.layers (report.json, layers/*.png RGBA, masks/*.png) - the
    current contract. Each step lights only its own layer's area: the layer is a mask over
    the main image, the compiled canvas as it stands after that step (track3's steps/*.png
    frame, rebuilt from the layer files with track3.layers.paint's rule
    canvas = canvas * (1 - alpha) + colour * alpha). The mask is the layer's own soft alpha
    with its opacity divided out, so feathered edges stay soft but a thin pass is not
    dimmed. Outside the mask is black (no light). Inside, a step looks exactly like
    track3's compiled frame for that step.
  - the older value partition from the GIMP API (public.json, layers/0N_*.png), filled
    with target_rgb.

For each step this measures where the step's visible region lands on the canvas, then
shows the step - optionally with the visible region outlined and stroke arrows along
stroke_dir_deg - and saves a camera capture, plus one grey-flash reference capture.
Uses the canvas quad saved by detect_canvas (run track1.selftest first, or pass --detect).

    uv run python -m track1.project_scene scenes/bobross-sunset-layers
    uv run python -m track1.project_scene scenes/bobross-sunset-layers --fill-only --no-landing
    uv run python -m track1.project_scene scenes/<job_id> --detect --any-shape
"""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

import cv2
import numpy as np

from . import Rig, overlay
from .selftest import LOG_DIR, PROJ_PX_PER_MM, landing_error


def scene_steps(scene: Path) -> list[dict]:
    """Steps in order: {name, mask, layer (or None), rgb (or None), stroke_deg (or None)}.

    stroke_deg is in overlay's convention (counter-clockwise, canvas up). track3.layers
    measures it in image coordinates (y down, so clockwise): negate it. It is an axis
    (0-180), not a direction: fold it into [-90, 90) so horizontal strokes point right
    and near-vertical ones point up.
    """
    report = scene / "report.json"
    if report.exists():                                  # layer stack (track3.layers)
        axis = lambda deg: (-deg + 90.0) % 180.0 - 90.0
        return [{"name": s["name"].lower().replace(" ", "-"), "mask": scene / s["mask_path"],
                 "layer": scene / s["layer_path"] if s.get("layer_path") else None,
                 "rgb": s.get("target_rgb"),
                 "stroke_deg": axis(float(s["stroke_dir_deg"])) if "stroke_dir_deg" in s else None}
                for s in json.loads(report.read_text())["steps"]]
    public = scene / "public.json"
    if public.exists():                                  # value partition (GIMP API)
        return [{"name": s["name"], "mask": scene / "layers" / Path(s["path"]).name, "layer": None,
                 "rgb": s.get("target_rgb"), "stroke_deg": None}
                for s in json.loads(public.read_text())["steps"]]
    return [{"name": p.stem, "mask": p, "layer": None, "rgb": None, "stroke_deg": None}
            for p in sorted((scene / "layers").glob("*.png"))]


def compiled_frames(layer_paths, bare=None):
    """BGR float canvas after each layer, as track3.layers.paint lays them down:
    canvas = canvas * (1 - alpha) + colour * alpha, from bare canvas."""
    if bare is None:
        from track3.layers import BARE_CANVAS as bare
    canvas, frames = None, []
    for path in layer_paths:
        rgba = cv2.imread(str(path), cv2.IMREAD_UNCHANGED).astype(np.float32)   # BGRA
        if canvas is None:
            canvas = np.full(rgba.shape[:2] + (3,), float(bare), np.float32)
        alpha = rgba[..., 3:] / 255.0
        canvas = canvas * (1.0 - alpha) + rgba[..., :3] * alpha
        frames.append(canvas.copy())
    return frames


def masked_step(frame, layer_path, opacity):
    """The compiled frame, lit only where this layer is painted (BGR uint8). Mask = the
    layer's alpha / its opacity: 1 inside the pass, soft across its feathered edge."""
    alpha = cv2.imread(str(layer_path), cv2.IMREAD_UNCHANGED)[..., 3:].astype(np.float32) / 255.0
    mask = np.clip(alpha / max(opacity, 1e-3), 0.0, 1.0)
    return np.clip(np.round(frame * mask), 0, 255).astype(np.uint8)


def step_overlay(step, image=None, fill_only=False):
    """(region to fill, style) for one step. `image`: the step's masked frame (layer stacks)."""
    style = {"outline": not fill_only, "outline_px": 3,
             "arrows": not fill_only and step["stroke_deg"] is not None,
             "stroke_dir_deg": step["stroke_deg"] or 0.0}
    if image is None:                                     # value partition: flat target colour
        style.update(fill_rgb=tuple(step["rgb"]) if step["rgb"] else (255, 255, 255), fill_alpha=0.8)
        return step["mask"], style
    style.update(fill_image=image, fill_alpha=1.0, outline_mask=step["mask"])
    return np.full(image.shape[:2], 255, np.uint8), style


def step_images(scene: Path, steps, mode="steps"):
    """What to project for each layer-stack step; None for value partitions.

    mode "steps":  track3's own steps/*.png frame - the whole picture after that step.
    mode "layers": the compiled frame lit only where that step's layer is (masked_step).
    """
    report = scene / "report.json"
    if not report.exists():
        return [None] * len(steps)
    rows = json.loads(report.read_text())["steps"]
    if mode == "steps":
        return [cv2.imread(str(scene / r["step_path"])) for r in rows]
    frames = compiled_frames([s["layer"] for s in steps])
    return [masked_step(f, s["layer"], r["opacity"]) for f, s, r in zip(frames, steps, rows)]


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("scene", type=Path)
    ap.add_argument("--detect", action="store_true", help="detect (or click) the canvas first")
    ap.add_argument("--any-shape", action="store_true", help="don't require the canvas_mm proportions")
    ap.add_argument("--hold", type=float, default=1.5, help="seconds to leave each step up")
    ap.add_argument("--fill-only", action="store_true", help="no outlines or arrows (for colour checks)")
    ap.add_argument("--no-landing", action="store_true", help="skip the per-step landing measurement")
    ap.add_argument("--projector", choices=["lightguide", "direct"], help="override rig_settings")
    ap.add_argument("--no-colour-correct", action="store_true", help="send colours uncorrected")
    ap.add_argument("--layers", action="store_true", help="light only each step's layer area, not the whole step")
    args = ap.parse_args()

    steps = scene_steps(args.scene)
    images = step_images(args.scene, steps, mode="layers" if args.layers else "steps")
    LOG_DIR.mkdir(exist_ok=True)
    stamp = time.strftime("%Y%m%d_%H%M%S")
    save = lambda tag, img: cv2.imwrite(str(LOG_DIR / f"scene_{stamp}_{tag}.jpg"), img)
    rig = Rig(args.projector)
    if args.no_colour_correct:
        rig.profile = None
    print(f"colour correction: {'on (k=%.2f)' % rig.profile.k if rig.profile else 'off'}")
    if args.any_shape:
        rig.settings["canvas_mm"] = None
    try:
        if args.detect:
            rig.detect_canvas()
        size = rig.canvas_size()
        print(f"canvas {size[0]}x{size[1]} projector px; {len(steps)} steps from {args.scene}")
        save(f"00_grey{rig.settings['flash_level']}", rig.flash_capture())
        if not args.no_landing:
            print(f"  {'step':<26} {'IoU':>6} {'mean mm':>8} {'p95 mm':>7}")
        for i, (step, image) in enumerate(zip(steps, images), 1):
            if not args.no_landing:
                mask = cv2.resize(overlay.load_mask(step["mask"]), size, interpolation=cv2.INTER_NEAREST)
                iou, dist, _, _ = landing_error(rig, mask)
                if len(dist):
                    mean, p95 = dist.mean() / PROJ_PX_PER_MM, np.percentile(dist, 95) / PROJ_PX_PER_MM
                    print(f"  {i:>2} {step['name']:<23} {iou:6.3f} {mean:8.2f} {p95:7.2f}")
                else:
                    print(f"  {i:>2} {step['name']:<23} {iou:6.3f}      n/a     n/a  (no boundary inside the canvas)")

            rig.project_overlay(*step_overlay(step, image, args.fill_only))
            time.sleep(max(args.hold, rig.settings["flash_settle_s"]))
            save(f"{i:02d}_{step['name']}", rig.frame())
        print(f"captures in {LOG_DIR} (scene_{stamp}_*.jpg)")
    finally:
        rig.clear()
        rig.close()


if __name__ == "__main__":
    main()
