"""
One command: reference image -> Track 3 layers -> projected step by step on the canvas.

    uv run python -m track1.show fixtures/spike_c/bobross-sunset.jpg
    uv run python -m track1.show my_painting.jpg --detect        # click the paper's corners first
    uv run python -m track1.show my_painting.jpg --remote        # GIMP server (5-band partition)
    uv run python -m track1.show my_painting.jpg --projector direct   # if a window covers LightGuide

1. Decompose. By default track3.layers (the layer stack, run locally, ~40 s) into
   scenes/<image name>-layers/; reused on later runs unless --redo. With --remote the image
   goes to the GIMP API instead (REMOTE_API.md; token in ~/.cache/rob-boss/agent-api-token)
   and comes back as the older five-band partition.
2. Project. Each step shows track3's step frame (steps/NN_*.png): the whole picture as it
   stands after that step, colour-corrected for the projector. --layers instead lights
   only that step's layer area of it (see track1/project_scene.py).
3. Step through it in the control window on the main screen:
       SPACE / right arrow / N  next step       left arrow / B  previous step
       O  outline + stroke arrows on/off        ESC / Q  quit
"""

from __future__ import annotations

import argparse
import time
from pathlib import Path

import cv2
import numpy as np

from . import Rig
from .project_scene import scene_steps, step_images, step_overlay

ROOT = Path(__file__).resolve().parent.parent
CONTROL = "PalettePilot - SPACE next, B back, O outline, ESC quit"
NEXT, PREV = (32, ord("n"), ord("N"), 2555904), (ord("b"), ord("B"), 2424832)   # waitKeyEx codes


def decompose(image: Path, remote: bool, redo: bool) -> Path:
    if remote:
        from track3.remote import Remote
        client = Remote()
        print(f"sending {image.name} to the GIMP API at {client.base} ...")
        public = client.process(image)
        scene = client.download_scene(public)
        print(f"  job {public['job_id']}: {len(public['steps'])} steps -> {scene}")
        return scene
    scene = ROOT / "scenes" / f"{image.stem}-layers"
    if (scene / "report.json").exists() and not redo:
        print(f"using existing layers in {scene} (--redo to decompose again)")
        return scene
    from track3.layers import decompose as layers_decompose
    print(f"decomposing {image.name} with track3.layers (about 40 s) ...")
    t = time.monotonic()
    report = layers_decompose(image.resolve(), scene)
    print(f"  {report['stage_count']} steps in {time.monotonic() - t:.0f} s -> {scene}")
    return scene


def control_panel(step, i, n, preview, outline):
    """Small window on the main screen: what's projected now, and the keys."""
    h = 360
    thumb = cv2.resize(preview, (int(preview.shape[1] * h / preview.shape[0]), h)) if preview is not None \
        else np.zeros((h, 540, 3), np.uint8)
    bar = np.zeros((90, thumb.shape[1], 3), np.uint8)
    cv2.putText(bar, f"step {i} of {n}: {step['name']}", (12, 34), cv2.FONT_HERSHEY_SIMPLEX, 0.9, (255, 255, 255), 2)
    cv2.putText(bar, f"SPACE next  B back  O outline ({'on' if outline else 'off'})  ESC quit",
                (12, 72), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (180, 180, 180), 1)
    return np.vstack([bar, thumb])


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("image", type=Path, help="reference image (jpg/png)")
    ap.add_argument("--remote", action="store_true", help="decompose on the GIMP server instead of locally")
    ap.add_argument("--redo", action="store_true", help="decompose again even if layers exist")
    ap.add_argument("--detect", action="store_true", help="find (or click) the canvas before projecting")
    ap.add_argument("--any-shape", action="store_true", help="with --detect: don't require letter-paper proportions")
    ap.add_argument("--projector", choices=["lightguide", "direct"], help="override rig_settings")
    ap.add_argument("--outline", action="store_true", help="start with outlines and stroke arrows on")
    ap.add_argument("--layers", action="store_true", help="light only each step's layer area, not the whole step")
    args = ap.parse_args()
    if not args.image.exists():
        ap.error(f"{args.image} not found")

    scene = decompose(args.image, args.remote, args.redo)
    steps = scene_steps(scene)
    images = step_images(scene, steps, mode="layers" if args.layers else "steps")

    rig = Rig(args.projector)
    try:
        if args.any_shape:
            rig.settings["canvas_mm"] = None
        if args.detect:
            rig.detect_canvas()
        i, outline, shown = 0, args.outline, None
        cv2.namedWindow(CONTROL)
        while True:
            if shown != (i, outline):
                rig.project_overlay(*step_overlay(steps[i], images[i], fill_only=not outline))
                shown = (i, outline)
                cv2.imshow(CONTROL, control_panel(steps[i], i + 1, len(steps), images[i], outline))
            key = cv2.waitKeyEx(50)
            if key in (27, ord("q"), ord("Q")) or cv2.getWindowProperty(CONTROL, cv2.WND_PROP_VISIBLE) < 1:
                break
            if key in NEXT:
                i = min(i + 1, len(steps) - 1)
            elif key in PREV:
                i = max(i - 1, 0)
            elif key in (ord("o"), ord("O")):
                outline = not outline
    finally:
        rig.clear()
        rig.close()
        cv2.destroyAllWindows()


if __name__ == "__main__":
    main()
