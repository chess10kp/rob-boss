"""
Read painted swatches under projected light and score the colour against a
ColorChecker.  (Spike B)

The projector contaminates every pixel the camera sees. This measures how much,
and whether blanking the projector before a capture fixes it.

Setup: a 24-patch ColorChecker (classic layout) and the swatches lie flat on the
work surface inside the camera view. Room lights on and unchanged - with the
projector blanked, room light is the only illuminant.

Flow:
  1. Projector goes black; click the ColorChecker's outer corners in order
     TL (dark skin), TR (bluish green), BR (black), BL (white), then the centre of
     each swatch. Saved to swatch_marks.json for reruns.
  2. Four captures: blank, live (a guide colour projected over everything),
     blank again (blank-before-capture, as the loop would do), and projector white.
  3. A colour-correction matrix (camera RGB -> sRGB) is fitted on the checker in
     the first blank capture and applied to every capture.

Reported:
  - Accuracy: leave-one-out dE2000 on the 24 checker patches (each patch is
    predicted by a matrix fitted on the other 23). This is the PLAN.md criterion.
  - Contamination: dE2000 of each swatch, live vs blank. Shows why blanking matters.
  - Blank-before-capture: dE2000 of each swatch, blank-after-live vs first blank.
  - Projector-as-flash: the same leave-one-out accuracy with the projector white as
    the light source, for rooms too dark to read colour with the projector blanked.

Pass (PLAN.md Spike B): dE < 10 on the blanked capture.

No ColorChecker? --no-checker clicks one clean paper patch instead and uses it
as the white reference. That still measures contamination and blank recovery,
but not absolute accuracy.

Room too dark? --flash LEVEL lights the reference captures with a projected grey
instead of blanking, so the projector is the (repeatable) light source. The
loop's step becomes flash-before-capture instead of blank-before-capture.

Usage:
    uv run config/swatch_check.py
    uv run config/swatch_check.py --no-checker --swatch-names blue,black
    uv run config/swatch_check.py --no-checker --reuse-marks --swatch-px 35 --flash 190
    uv run config/swatch_check.py --reuse-marks
Keys (clicking): left-click = place point, arrow keys = nudge, U = undo, ENTER = accept
"""

import argparse
import json
import time

import cv2
import numpy as np

import corner_check
import rig

MARKS_PATH = rig.CONFIG_DIR / "swatch_marks.json"
LOG_DIR = rig.CONFIG_DIR / "logs"
CHECKER_CORNERS = ["TL dark skin", "TR bluish green", "BR black", "BL white"]

# Classic 24-patch ColorChecker, row-major from dark skin, as 8-bit sRGB (X-Rite published values)
CHECKER_SRGB = np.array([
    [115, 82, 68], [194, 150, 130], [98, 122, 157], [87, 108, 67], [133, 128, 177], [103, 189, 170],
    [214, 126, 44], [80, 91, 166], [193, 90, 99], [94, 60, 108], [157, 188, 64], [224, 163, 46],
    [56, 61, 150], [70, 148, 73], [175, 54, 60], [231, 199, 31], [187, 86, 149], [8, 133, 161],
    [243, 243, 242], [200, 200, 200], [160, 160, 160], [122, 122, 121], [85, 85, 85], [52, 52, 52],
], np.float64)
WHITE_PATCH = 18


# ---------------- colour maths ----------------

def srgb_to_linear(c):
    c = np.asarray(c, np.float64) / 255.0
    return np.where(c <= 0.04045, c / 12.92, ((c + 0.055) / 1.055) ** 2.4)


def linear_to_lab(rgb):
    """Linear sRGB (D65) -> CIELAB."""
    m = np.array([[0.4124, 0.3576, 0.1805], [0.2126, 0.7152, 0.0722], [0.0193, 0.1192, 0.9505]])
    xyz = np.clip(rgb, 0, None) @ m.T / np.array([0.95047, 1.0, 1.08883])
    f = np.where(xyz > (6 / 29) ** 3, np.cbrt(xyz), xyz / (3 * (6 / 29) ** 2) + 4 / 29)
    return np.stack([116 * f[..., 1] - 16, 500 * (f[..., 0] - f[..., 1]), 200 * (f[..., 1] - f[..., 2])], -1)


def delta_e2000(lab1, lab2):
    L1, a1, b1 = np.moveaxis(np.asarray(lab1, np.float64), -1, 0)
    L2, a2, b2 = np.moveaxis(np.asarray(lab2, np.float64), -1, 0)
    cb = (np.hypot(a1, b1) + np.hypot(a2, b2)) / 2
    g = 0.5 * (1 - np.sqrt(cb ** 7 / (cb ** 7 + 25.0 ** 7)))
    a1p, a2p = (1 + g) * a1, (1 + g) * a2
    c1p, c2p = np.hypot(a1p, b1), np.hypot(a2p, b2)
    h1p, h2p = np.degrees(np.arctan2(b1, a1p)) % 360, np.degrees(np.arctan2(b2, a2p)) % 360
    zero = c1p * c2p == 0
    dh = h2p - h1p
    dh = np.where(dh > 180, dh - 360, np.where(dh < -180, dh + 360, dh))
    dh = np.where(zero, 0, dh)
    dL, dC = L2 - L1, c2p - c1p
    dH = 2 * np.sqrt(c1p * c2p) * np.sin(np.radians(dh / 2))
    Lb, Cb = (L1 + L2) / 2, (c1p + c2p) / 2
    hs = h1p + h2p
    hb = np.where(zero, hs, np.where(np.abs(h1p - h2p) <= 180, hs / 2,
                                     np.where(hs < 360, (hs + 360) / 2, (hs - 360) / 2)))
    t = (1 - 0.17 * np.cos(np.radians(hb - 30)) + 0.24 * np.cos(np.radians(2 * hb))
         + 0.32 * np.cos(np.radians(3 * hb + 6)) - 0.20 * np.cos(np.radians(4 * hb - 63)))
    rc = 2 * np.sqrt(Cb ** 7 / (Cb ** 7 + 25.0 ** 7))
    rt = -np.sin(np.radians(60 * np.exp(-((hb - 275) / 25) ** 2))) * rc
    sl = 1 + 0.015 * (Lb - 50) ** 2 / np.sqrt(20 + (Lb - 50) ** 2)
    sc, sh = 1 + 0.045 * Cb, 1 + 0.015 * Cb * t
    return np.sqrt((dL / sl) ** 2 + (dC / sc) ** 2 + (dH / sh) ** 2 + rt * (dC / sc) * (dH / sh))


def fit_ccm(cam_lin, ref_lin):
    """Affine colour-correction (4x3): [r g b 1] @ M ~ reference linear sRGB.
    The offset row absorbs flare and the camera's black level."""
    x = np.column_stack([cam_lin, np.ones(len(cam_lin))])
    m, *_ = np.linalg.lstsq(x, ref_lin, rcond=None)
    return m


def apply_ccm(m, cam_lin):
    return np.column_stack([cam_lin, np.ones(len(cam_lin))]) @ m


def loo_delta_e(cam_lin, ref_lin):
    """Leave-one-out: each patch predicted by a CCM fitted on the others."""
    ref_lab = linear_to_lab(ref_lin)
    out = []
    for i in range(len(cam_lin)):
        keep = np.arange(len(cam_lin)) != i
        pred = apply_ccm(fit_ccm(cam_lin[keep], ref_lin[keep]), cam_lin[i:i + 1])
        out.append(delta_e2000(linear_to_lab(pred), ref_lab[i:i + 1])[0])
    return np.array(out)


# ---------------- sampling ----------------

def checker_centres(corners):
    """Centres of the 6x4 patches (camera px) and a sampling half-size, from the
    four clicked outer corners."""
    unit = np.float32([[0, 0], [1, 0], [1, 1], [0, 1]])
    H = cv2.getPerspectiveTransform(unit, np.float32(corners))
    grid = np.float32([[(c + 0.5) / 6, (r + 0.5) / 4] for r in range(4) for c in range(6)])
    centres = cv2.perspectiveTransform(grid.reshape(-1, 1, 2), H).reshape(-1, 2)
    pitch = np.linalg.norm(centres[1] - centres[0])
    return centres, max(3, int(pitch * 0.25))         # sample the middle ~half of each patch


def sample(frame, centres, half):
    """Mean linear RGB (as R, G, B) in a square around each centre; also flags clipping."""
    lin = srgb_to_linear(frame[..., ::-1])
    out, clipped = [], []
    for x, y in centres:
        x, y = int(round(x)), int(round(y))
        patch = frame[y - half:y + half + 1, x - half:x + half + 1]
        out.append(lin[y - half:y + half + 1, x - half:x + half + 1].reshape(-1, 3).mean(0))
        clipped.append(bool((patch >= 250).any(-1).mean() > 0.05))
    return np.array(out), clipped


def save_marks(marks, cam_size):
    MARKS_PATH.write_text(json.dumps({**{k: (None if v is None else np.asarray(v).tolist())
                                         for k, v in marks.items() if k != "names"},
                                      "names": marks["names"], "cam_size": list(cam_size)}, indent=2))


def load_marks(cam_size):
    if not MARKS_PATH.exists():
        raise SystemExit(f"{MARKS_PATH.name} not found - run once without --reuse-marks.")
    data = json.loads(MARKS_PATH.read_text())
    if tuple(data["cam_size"]) != tuple(cam_size):
        raise SystemExit(f"marks were clicked at camera {data['cam_size']}, now {cam_size}.")
    return {"checker": None if data.get("checker") is None else np.float32(data["checker"]),
            "paper": None if data.get("paper") is None else np.float32(data["paper"]),
            "swatches": np.float32(data["swatches"]), "names": data["names"]}


def click_marks(frame, use_checker, names):
    head = CHECKER_CORNERS if use_checker else ["paper white (between lines)"]
    pts = corner_check.click_tape_marks(frame, head + names, "click reference points, then swatch centres")
    return {"checker": pts[:4] if use_checker else None,
            "paper": None if use_checker else pts[:1],
            "swatches": pts[len(head):], "names": names}


# ---------------- run ----------------

def capture(proj, cap, img, settle_ms):
    proj.show(img, settle_ms)
    return rig.grab_frame(cap)


def annotate(frame, boxes):
    """boxes: [(label, (x, y), half, colour)] in camera px."""
    disp, scale = rig.fit_preview(frame)
    for label, (x, y), half, colour in boxes:
        p0 = (int((x - half) * scale), int((y - half) * scale))
        p1 = (int((x + half) * scale), int((y + half) * scale))
        cv2.rectangle(disp, p0, p1, colour, 2 if label else 1)
        if label:
            cv2.putText(disp, label, (p0[0], p0[1] - 6), cv2.FONT_HERSHEY_SIMPLEX, 0.6, colour, 2)
    return disp


def level(lin_rgb):
    """Approximate 8-bit brightness of a linear RGB triple."""
    return 255 * float(np.clip(lin_rgb, 0, 1).mean()) ** (1 / 2.2)


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    rig.add_rig_args(parser)
    parser.add_argument("--no-checker", action="store_true",
                        help="no ColorChecker: click a clean paper patch as the white reference. "
                             "Measures projector contamination and blank recovery, not absolute accuracy")
    parser.add_argument("--swatch-names", default="S1,S2,S3", help="comma-separated, in click order")
    parser.add_argument("--swatch-px", type=int, default=25, help="sampling half-size per swatch, camera px")
    parser.add_argument("--paper-px", type=int, default=8,
                        help="sampling half-size for the paper patch (keep it between ruled lines)")
    parser.add_argument("--live-rgb", default="94,142,183",
                        help="guide colour projected for the live capture (R,G,B)")
    parser.add_argument("--flash", type=int, default=None, metavar="LEVEL",
                        help="light the reference captures with a projected grey 0-255 instead of "
                             "blanking (for rooms too dark to read colour by room light)")
    parser.add_argument("--reuse-marks", action="store_true", help=f"load {MARKS_PATH.name}")
    parser.add_argument("--tolerance", type=float, default=10.0, help="dE2000 pass threshold")
    args = parser.parse_args()
    if rig.handle_list(args):
        return

    proj = rig.Projector(rig.pick_display(args.display))
    cap = rig.open_camera(args.camera, args.cam_res)
    cam_size = rig.camera_size(cap)
    use_checker = not args.no_checker

    if args.reuse_marks:
        marks = load_marks(cam_size)
        if (marks["checker"] is not None) != use_checker:
            raise SystemExit("saved marks are for the other mode (--no-checker) - click again.")
    else:
        names = [n.strip() for n in args.swatch_names.split(",") if n.strip()]
        frame = rig.blank_and_capture(proj, cap, settle_ms=args.settle_ms)
        marks = click_marks(frame, use_checker, names)
        save_marks(marks, cam_size)
    names, swatches = marks["names"], marks["swatches"]

    r, g, b = (int(v) for v in args.live_rgb.split(","))
    black = proj.black
    # the reference capture: projector blanked (room light only), or a grey flash
    ref_img = black if args.flash is None else np.full_like(black, args.flash)
    ref_desc = "blank" if args.flash is None else f"grey flash {args.flash}"
    print(f"Capturing: reference ({ref_desc}), live, reference-after-live, white ...")
    shots = {"ref": capture(proj, cap, ref_img, args.settle_ms),
             "live": capture(proj, cap, np.full_like(black, (b, g, r)), args.settle_ms),
             "ref_after_live": capture(proj, cap, ref_img, args.settle_ms),
             "white": capture(proj, cap, np.full_like(black, 255), args.settle_ms)}
    proj.blank()
    cap.release()
    cv2.destroyAllWindows()

    # reference points: checker patches, or the single paper patch
    if use_checker:
        ref_c, ref_half = checker_centres(marks["checker"])
        white_idx = WHITE_PATCH
    else:
        ref_c, ref_half, white_idx = marks["paper"], args.paper_px, 0
    ref, sw, clip = {}, {}, {}
    for name, frame in shots.items():
        ref[name], c1 = sample(frame, ref_c, ref_half)
        sw[name], c2 = sample(frame, swatches, args.swatch_px)
        clip[name] = c1[white_idx] or any(c2)

    stamp = time.strftime("%Y%m%d_%H%M%S")
    LOG_DIR.mkdir(exist_ok=True)
    boxes = [("", c, ref_half, (0, 255, 0)) for c in ref_c] + \
            [(n, c, args.swatch_px, (0, 255, 255)) for n, c in zip(names, swatches)]
    for name, frame in shots.items():
        cv2.imwrite(str(LOG_DIR / f"swatch_{stamp}_{name}.jpg"), annotate(frame, boxes))

    print("\nWhite reference brightness per capture:")
    for name in shots:
        lv = level(ref[name][white_idx])
        note = "  CLIPPED - too bright; lower --flash if this is a reference capture" if clip[name] else \
               "  DARK - readings unreliable; add room light or use --flash" if lv < 60 else ""
        print(f"  {name:>17}: ~{lv:.0f}/255{note}")

    # Calibrate once, on the first reference capture, then apply that to every capture -
    # the loop can't recalibrate per frame.
    result = {"mode": "checker" if use_checker else "paper", "reference": ref_desc,
              "live_rgb": [r, g, b], "cam_size": list(cam_size), "clipped": clip}
    if use_checker:
        ref_lin = srgb_to_linear(CHECKER_SRGB)
        loo = loo_delta_e(ref["ref"], ref_lin)
        ccm = fit_ccm(ref["ref"], ref_lin)
        to_lab = lambda rgb: linear_to_lab(apply_ccm(ccm, rgb))
        loo_white = loo_delta_e(ref["white"], ref_lin)
        result.update(loo_ref=loo.tolist(), loo_white=loo_white.tolist())
    else:
        paper = ref["ref"][0]                                  # paper -> D65 white (von Kries)
        to_lab = lambda rgb: linear_to_lab(rgb / paper)

    sw_ref = to_lab(sw["ref"])
    de_live = delta_e2000(to_lab(sw["live"]), sw_ref)
    de_reblank = delta_e2000(to_lab(sw["ref_after_live"]), sw_ref)
    paper_live = delta_e2000(to_lab(ref["live"][white_idx:white_idx + 1]),
                             to_lab(ref["ref"][white_idx:white_idx + 1]))[0]

    if use_checker:
        print(f"\nAccuracy vs ColorChecker, reference = {ref_desc} (leave-one-out dE2000):")
        print(f"  mean {loo.mean():.1f}   max {loo.max():.1f}  (patch {loo.argmax() + 1})")
        print(f"Projector as flash (white, own CCM): mean {loo_white.mean():.1f}  max {loo_white.max():.1f}")
    else:
        print("\nNo ColorChecker: colours are relative to the paper (paper = white). "
              "Absolute accuracy is NOT measured.")

    print(f"\nSwatches, dE2000 vs the first reference capture ({ref_desc}); "
          f"white reference shifts {paper_live:.1f} under live light:")
    print(f"  {'swatch':>8}   L*     a*     b*     live   ref-after-live")
    for name, (L, a, bb), dl, dr in zip(names, sw_ref, de_live, de_reblank):
        print(f"  {name:>8} {L:6.1f} {a:6.1f} {bb:6.1f} {dl:7.1f} {dr:8.1f}")

    ok = de_reblank.max() < args.tolerance and (not use_checker or loo.mean() < args.tolerance)
    verdict = "PASS" if ok else "FAIL"
    if level(ref["ref"][white_idx]) < 60:          # too dark to read colour: no verdict either way
        ok, verdict = False, "INCONCLUSIVE (reference capture too dark - add room light or use --flash)"
    elif clip["ref"]:
        ok, verdict = False, "INCONCLUSIVE (reference capture clipped - lower --flash)"
    elif not use_checker:
        verdict += " (contamination + recovery only; no absolute-accuracy check)"
    print(f"\nSpike B: projector contaminates swatches by up to dE {de_live.max():.1f}; "
          f"{ref_desc} before capture recovers to within dE {de_reblank.max():.1f} -> {verdict}")

    result.update(names=names, swatch_lab=sw_ref.tolist(), swatch_de_live=de_live.tolist(),
                  swatch_de_ref_after_live=de_reblank.tolist(), white_de_live=float(paper_live),
                  passed=bool(ok))
    (LOG_DIR / f"swatch_{stamp}.json").write_text(json.dumps(result, indent=2))
    print(f"Logged to {LOG_DIR / f'swatch_{stamp}.json'} (+ annotated captures)")


if __name__ == "__main__":
    main()
