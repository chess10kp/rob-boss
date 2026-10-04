"""
Live check of Track 1 on the rig: find the canvas, project a known shape through the
backend, capture it back and measure where it landed; then run each overlay style,
the correction flash and capture_canvas.

Put a canvas (or a plain sheet of paper standing in for one) flat on the mat, inside
the projected area.

    uv run python -m track1.selftest
    uv run python -m track1.selftest --manual       # click the canvas corners yourself
    uv run python -m track1.selftest --projector direct          # LightGuide not running
    uv run python -m track1.selftest --camera-source lightguide  # LightGuide's camera
"""

from __future__ import annotations

import argparse
import time

import cv2
import numpy as np

from config import rig as config_rig
from . import Rig, geometry

LOG_DIR = config_rig.CONFIG_DIR / "logs"
PROJ_PX_PER_MM = 4.29         # LightGuide 'PRJ - 2D Flat Surface' scale on the mat (approximate)


def test_mask(size):
    """An asymmetric L, so a flipped or rotated mapping can't pass."""
    w, h = size
    m = np.zeros((h, w), np.uint8)
    m[int(0.1 * h):int(0.9 * h), int(0.1 * w):int(0.4 * w)] = 255
    m[int(0.1 * h):int(0.35 * h), int(0.1 * w):int(0.9 * w)] = 255
    return m


def landing_error(rig, mask):
    """Project the mask as solid white, capture lit and unlit, and compare in canvas space."""
    rig.project_overlay(mask, {"fill_alpha": 1.0, "outline": False})
    time.sleep(rig.settings["flash_settle_s"])
    lit = rig.frame()
    rig.clear()
    time.sleep(rig.settings["flash_settle_s"])
    dark = rig.frame()
    size = (mask.shape[1], mask.shape[0])
    diff = cv2.subtract(cv2.cvtColor(geometry.rectify(lit, rig.quad_cam, size), cv2.COLOR_BGR2GRAY),
                        cv2.cvtColor(geometry.rectify(dark, rig.quad_cam, size), cv2.COLOR_BGR2GRAY))
    _, seen = cv2.threshold(cv2.GaussianBlur(diff, (5, 5), 0), 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    want, got = mask > 127, seen > 127
    iou = (want & got).sum() / max(1, (want | got).sum())
    # distance from each seen-boundary pixel to the intended boundary (canvas px ~ projector px)
    edge = lambda m: cv2.morphologyEx(m.astype(np.uint8), cv2.MORPH_GRADIENT, np.ones((3, 3), np.uint8)) > 0
    dist = cv2.distanceTransform((~edge(want)).astype(np.uint8), cv2.DIST_L2, 5)[edge(got)]
    return iou, dist, lit, seen


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--projector", choices=["lightguide", "direct"])
    ap.add_argument("--camera-source", choices=["lightguide", "direct"])
    ap.add_argument("--manual", action="store_true", help="click the canvas corners instead of detecting")
    ap.add_argument("--any-shape", action="store_true",
                    help="don't require the canvas_mm proportions (e.g. testing with a non-letter sheet)")
    args = ap.parse_args()

    LOG_DIR.mkdir(exist_ok=True)
    stamp = time.strftime("%Y%m%d_%H%M%S")
    save = lambda name, img: cv2.imwrite(str(LOG_DIR / f"track1_{stamp}_{name}.jpg"), img)

    t0 = time.monotonic()
    rig = Rig(args.projector, args.camera_source)
    if args.any_shape:
        rig.settings["canvas_mm"] = None
    print(f"projector: {rig.projector_kind} {rig.proj_size[0]}x{rig.proj_size[1]}   camera: {rig.camera_kind}")
    try:
        quad = rig.detect_canvas(manual=args.manual)
        print(f"camera {rig.cam_size[0]}x{rig.cam_size[1]} (first frame after {time.monotonic() - t0:.1f}s)")
        w, h = rig.canvas_size()
        print(f"canvas: {w}x{h} projector px  ~ {w / PROJ_PX_PER_MM:.0f} x {h / PROJ_PX_PER_MM:.0f} mm")
        print("  camera quad:", np.round(quad, 1).tolist())

        mask = test_mask((w, h))
        iou, dist, lit, seen = landing_error(rig, mask)
        save("landing_camera", lit)
        save("landing_seen_vs_mask", np.dstack([seen, mask, np.zeros_like(mask)]))   # green=wanted, red=seen
        if len(dist):
            print(f"landing: IoU {iou:.3f}; boundary offset mean {dist.mean() / PROJ_PX_PER_MM:.2f} mm, "
                  f"95th pct {np.percentile(dist, 95) / PROJ_PX_PER_MM:.2f} mm "
                  f"-> {'PASS' if np.percentile(dist, 95) / PROJ_PX_PER_MM <= 5 else 'FAIL'} (5 mm)")
        else:
            print("landing: projected shape not seen - check the canvas is inside the projected area")

        styles = {
            "fill": {"fill_rgb": (94, 142, 183), "fill_alpha": 0.8, "outline": False},
            "outline": {"fill": False, "outline_px": 4},
            "arrows": {"fill": False, "outline": True, "arrows": True, "stroke_dir_deg": 30},
            "all": {"fill_rgb": (94, 142, 183), "fill_alpha": 0.5, "arrows": True, "stroke_dir_deg": 0},
        }
        for name, style in styles.items():
            t = time.monotonic()
            rig.project_overlay(mask, style)
            shown = time.monotonic() - t
            time.sleep(rig.settings["flash_settle_s"])
            save(f"style_{name}", rig.frame())
            print(f"style {name:8} shown in {shown:.2f}s")

        t = time.monotonic()
        rig.flash_correction(mask, times=3)
        print(f"correction flash x3 took {time.monotonic() - t:.1f}s")

        t = time.monotonic()
        canvas_img = rig.capture_canvas()
        print(f"capture_canvas: {canvas_img.shape[1]}x{canvas_img.shape[0]} in {time.monotonic() - t:.2f}s "
              "(flash, grab, restore overlay)")
        save("capture_canvas", canvas_img)
        print(f"images in {LOG_DIR} (track1_{stamp}_*.jpg)")
    finally:
        rig.clear()
        rig.close()


if __name__ == "__main__":
    main()
