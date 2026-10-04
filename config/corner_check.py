"""
Project a filled rectangle onto tape marks and measure the error at all four
corners in millimetres.  (Spike A, part 2)

Prereq: run checkerboard.py first, with the checkerboard projected onto the
canvas surface, so cam_to_proj_homography.npz is valid at canvas height.

Setup: put four L-shaped tape marks on the canvas roughly at the corners of a
rectangle, with the L opening inward so the tape sits *outside* the rectangle and
its inner vertex is the target point. Tape under the projected fill darkens it and
biases detection. Measure the tape-to-tape width and height with a ruler.

Flow:
  1. Projector goes black; click the four tape points in the camera view in
     order TL, TR, BR, BL. They are saved to tape_marks.json for reruns.
  2. Each tape point is mapped camera -> projector through H, and a filled
     rectangle is projected with its corners on those points.
  3. The rectangle is found in the camera (lit minus blank frame). Its corners are
     mapped into the tape's mm plane, and the offset at each corner is printed.
     The rectangle stays projected, so you can check the same gaps with a ruler.
  4. M runs the stability check: re-measure every --interval s for --minutes,
     logging to config/logs/*.csv.

Pass (PLAN.md Spike A): <= 5 mm at every corner, stable across 10 min.

Usage:
    uv run config/corner_check.py --camera 1 --cam-res 3840x2160 --tape-w-mm 400 --tape-h-mm 300
    uv run config/corner_check.py --camera 1 --cam-res 3840x2160 --reuse-marks --monitor
Keys:  SPACE = measure,  M = 10-min stability run,  R = re-click tape marks,
       S = camera settings dialog,  ESC = quit
Clicking:  left-click = place point,  arrow keys = nudge last point 1 camera px,
           U = undo,  ENTER = accept
"""

import argparse
import csv
import itertools
import json
import time

import cv2
import numpy as np

import rig

CORNERS = ["TL", "TR", "BR", "BL"]
MARKS_PATH = rig.CONFIG_DIR / "tape_marks.json"
LOG_DIR = rig.CONFIG_DIR / "logs"
CLICK_WIN = "tape marks - click TL, TR, BR, BL"
LOUPE_R, LOUPE_ZOOM = 30, 5          # loupe shows a (2R+1)^2 camera-px patch at 5x
ARROWS = {2424832: (-1, 0), 2490368: (0, -1), 2555904: (1, 0), 2621440: (0, 1)}  # waitKeyEx codes


# ---------------- tape marks ----------------

def draw_loupe(disp, frame, center, pts):
    """Magnified camera-resolution patch around `center`, pasted top-left of `disp`."""
    size = 2 * LOUPE_R + 1
    patch = cv2.getRectSubPix(frame, (size, size), center)
    patch = cv2.resize(patch, None, fx=LOUPE_ZOOM, fy=LOUPE_ZOOM, interpolation=cv2.INTER_NEAREST)
    mid = size * LOUPE_ZOOM // 2
    cv2.line(patch, (mid, 0), (mid, patch.shape[0]), (0, 255, 255), 1)
    cv2.line(patch, (0, mid), (patch.shape[1], mid), (0, 255, 255), 1)
    for x, y in pts:
        lx = int((x - center[0] + LOUPE_R + 0.5) * LOUPE_ZOOM)
        ly = int((y - center[1] + LOUPE_R + 0.5) * LOUPE_ZOOM)
        cv2.circle(patch, (lx, ly), 6, (0, 255, 0), 2)
    h, w = min(patch.shape[0], disp.shape[0]), min(patch.shape[1], disp.shape[1])
    disp[:h, :w] = patch[:h, :w]


def click_tape_marks(frame):
    """Click the four tape points; returns them in full camera-pixel coords (TL, TR, BR, BL)."""
    base, scale = rig.fit_preview(frame)
    pts = []
    state = {"cursor": None, "focus": "cursor"}

    def on_mouse(event, x, y, flags, _):
        state["cursor"] = (x / scale, y / scale)
        state["focus"] = "cursor"
        if event == cv2.EVENT_LBUTTONDOWN and len(pts) < 4:
            pts.append([x / scale, y / scale])
            state["focus"] = "point"

    cv2.namedWindow(CLICK_WIN)
    cv2.setMouseCallback(CLICK_WIN, on_mouse)
    while True:
        disp = base.copy()
        for name, (x, y) in zip(CORNERS, pts):
            p = (int(x * scale), int(y * scale))
            cv2.circle(disp, p, 6, (0, 255, 0), 2)
            cv2.putText(disp, name, (p[0] + 8, p[1] - 8), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 255, 0), 2)
        center = pts[-1] if (state["focus"] == "point" and pts) else state["cursor"]
        if center is not None:
            draw_loupe(disp, frame, tuple(map(float, center)), pts)
        prompt = (f"click {CORNERS[len(pts)]}" if len(pts) < 4 else "ENTER to accept") + \
                 "   arrows=nudge  U=undo  ESC=quit"
        cv2.putText(disp, prompt, (10, disp.shape[0] - 12), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 255, 255), 2)
        cv2.imshow(CLICK_WIN, disp)

        key = cv2.waitKeyEx(20)
        if key == 27:
            raise SystemExit("aborted")
        if key in (ord("u"), ord("U")) and pts:
            pts.pop()
        if key in ARROWS and pts:
            dx, dy = ARROWS[key]
            pts[-1][0] += dx
            pts[-1][1] += dy
            state["focus"] = "point"
        if key in (13, 10) and len(pts) == 4:
            break
    cv2.destroyWindow(CLICK_WIN)
    return np.array(pts, np.float32)


def save_marks(tape_cam, w_mm, h_mm, cam_size):
    MARKS_PATH.write_text(json.dumps({
        "tape_cam": tape_cam.tolist(), "tape_w_mm": w_mm, "tape_h_mm": h_mm,
        "cam_size": list(cam_size)}, indent=2))


def load_marks(cam_size):
    if not MARKS_PATH.exists():
        raise SystemExit(f"{MARKS_PATH.name} not found - run once without --reuse-marks.")
    data = json.loads(MARKS_PATH.read_text())
    if tuple(data["cam_size"]) != tuple(cam_size):
        raise SystemExit(f"tape marks were clicked at camera {data['cam_size']}, now {cam_size}.")
    return np.array(data["tape_cam"], np.float32), data["tape_w_mm"], data["tape_h_mm"]


# ---------------- projected rectangle ----------------

def rect_image(proj, quad_proj, level):
    img = np.zeros((proj.h, proj.w, 3), np.uint8)
    # shift=4 -> 1/16 px vertex precision
    cv2.fillPoly(img, [np.round(quad_proj * 16).astype(np.int32)], (level,) * 3,
                 lineType=cv2.LINE_AA, shift=4)
    return img


def line_intersect(l1, l2):
    (p1, d1), (p2, d2) = l1, l2
    s, _ = np.linalg.solve(np.column_stack([d1, -d2]), p2 - p1)
    return p1 + s * d1


def edge_fit_corners(contour, approx):
    """Refine 4 polygon vertices by fitting a line to each edge's contour points."""
    pts = contour.reshape(-1, 2).astype(np.float32)
    lines = []
    for i in range(4):
        a, b = approx[i], approx[(i + 1) % 4]
        length = np.linalg.norm(b - a)
        u = (b - a) / length
        n = np.array([-u[1], u[0]])
        rel = pts - a
        t, d = rel @ u, np.abs(rel @ n)
        sel = (t > 0.1 * length) & (t < 0.9 * length) & (d < max(3.0, 0.02 * length))
        if sel.sum() < 5:
            return approx
        vx, vy, x0, y0 = cv2.fitLine(pts[sel], cv2.DIST_HUBER, 0, 0.01, 0.01).ravel()
        lines.append((np.array([x0, y0]), np.array([vx, vy])))
    # vertex i sits between edge i-1 and edge i
    return np.array([line_intersect(lines[i - 1], lines[i]) for i in range(4)], np.float32)


def detect_rect(lit, blank):
    """Corners of the projected rectangle in camera px, or None."""
    diff = cv2.subtract(cv2.cvtColor(lit, cv2.COLOR_BGR2GRAY), cv2.cvtColor(blank, cv2.COLOR_BGR2GRAY))
    diff = cv2.GaussianBlur(diff, (5, 5), 0)
    _, mask = cv2.threshold(diff, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (5, 5))
    mask = cv2.morphologyEx(cv2.morphologyEx(mask, cv2.MORPH_OPEN, kernel), cv2.MORPH_CLOSE, kernel)
    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)
    if not contours:
        return None
    c = max(contours, key=cv2.contourArea)
    peri = cv2.arcLength(c, True)
    for eps in (0.01, 0.02, 0.03, 0.05):
        approx = cv2.approxPolyDP(c, eps * peri, True)
        if len(approx) == 4:
            return edge_fit_corners(c, approx.reshape(-1, 2).astype(np.float32))
    return None


def match_order(det, ref):
    """Reorder detected corners to pair with ref (TL, TR, BR, BL) by least total distance."""
    best = min(itertools.permutations(range(4)),
               key=lambda p: sum(np.linalg.norm(det[j] - ref[i]) for i, j in enumerate(p)))
    return det[list(best)]


# ---------------- measurement ----------------

class Check:
    def __init__(self, proj, cap, H, tape_cam, w_mm, h_mm, args):
        self.proj, self.cap, self.args = proj, cap, args
        self.tape_cam = tape_cam
        self.mm_quad = np.array([[0, 0], [w_mm, 0], [w_mm, h_mm], [0, h_mm]], np.float32)
        self.H_mm = cv2.getPerspectiveTransform(tape_cam, self.mm_quad)   # camera px -> canvas mm
        quad_proj = cv2.perspectiveTransform(tape_cam.reshape(-1, 1, 2), H).reshape(-1, 2)
        self.rect = rect_image(proj, quad_proj, args.fill)

    def measure(self):
        """Blank -> capture, rectangle -> capture. Returns (err_mm[4,2] or None, overlay)."""
        blank = rig.blank_and_capture(self.proj, self.cap, settle_ms=self.args.settle_ms)
        self.proj.show(self.rect, self.args.settle_ms)
        lit = rig.grab_frame(self.cap)
        det = detect_rect(lit, blank)
        if det is None:
            return None, lit
        det = match_order(det, self.tape_cam)
        err = cv2.perspectiveTransform(det.reshape(-1, 1, 2), self.H_mm).reshape(-1, 2) - self.mm_quad
        return err, self.overlay(lit, det, err)

    def overlay(self, lit, det, err):
        disp, scale = rig.fit_preview(lit)
        for name, t, d, e in zip(CORNERS, self.tape_cam, det, err):
            tp, dp = tuple(int(v) for v in t * scale), tuple(int(v) for v in d * scale)
            cv2.circle(disp, tp, 7, (0, 255, 0), 2)              # tape mark
            cv2.drawMarker(disp, dp, (0, 0, 255), cv2.MARKER_CROSS, 14, 2)   # projected corner
            ok = np.linalg.norm(e) <= self.args.tolerance_mm
            cv2.putText(disp, f"{name} {np.linalg.norm(e):.1f}mm", (tp[0] + 10, tp[1] + 20),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 255, 0) if ok else (0, 0, 255), 2)
        return disp


def print_errors(err, tol):
    print("  corner    dx mm    dy mm   |err| mm")
    for name, (dx, dy) in zip(CORNERS, err):
        print(f"  {name:>6} {dx:8.2f} {dy:8.2f} {np.hypot(dx, dy):9.2f}")
    worst = float(np.linalg.norm(err, axis=1).max())
    print(f"  worst {worst:.2f} mm -> {'PASS' if worst <= tol else 'FAIL'} (tolerance {tol} mm)")
    return worst


def wait_until(t):
    """Pump the GUI (rectangle stays projected) until time t; False if ESC was pressed."""
    while time.time() < t:
        if (cv2.waitKey(100) & 0xFF) == 27:
            return False
    return True


def monitor(check, args):
    """Re-measure every interval for `minutes`; log to CSV and summarise error + drift."""
    LOG_DIR.mkdir(exist_ok=True)
    path = LOG_DIR / time.strftime("corner_check_%Y%m%d_%H%M%S.csv")
    samples, t0 = [], time.time()
    with open(path, "w", newline="") as f:
        out = csv.writer(f)
        out.writerow(["elapsed_s"] + [f"{c}_{k}" for c in CORNERS for k in ("dx_mm", "dy_mm", "err_mm")]
                     + ["worst_mm"])
        for i in range(int(args.minutes * 60 // args.interval) + 1):
            if not wait_until(t0 + i * args.interval):
                print("stability run aborted")
                break
            err, disp = check.measure()
            elapsed = time.time() - t0
            if err is None:
                cv2.imshow("camera", rig.fit_preview(disp)[0])
                print(f"[{elapsed:6.0f}s] rectangle not found")
                out.writerow([f"{elapsed:.1f}"])
                continue
            cv2.imshow("camera", disp)
            mags = np.linalg.norm(err, axis=1)
            samples.append(err)
            out.writerow([f"{elapsed:.1f}"] + [f"{v:.3f}" for e, m in zip(err, mags) for v in (*e, m)]
                         + [f"{mags.max():.3f}"])
            f.flush()
            print(f"[{elapsed:6.0f}s] " + "  ".join(f"{c} {m:.2f}" for c, m in zip(CORNERS, mags))
                  + f"   worst {mags.max():.2f} mm")

    print(f"Logged to {path}")
    if not samples:
        return
    s = np.array(samples)                                   # (samples, 4, 2)
    worst = float(np.linalg.norm(s, axis=2).max())
    drift = float(np.linalg.norm(s - s[0], axis=2).max())
    print(f"Stability: {len(s)} samples, worst error {worst:.2f} mm, "
          f"max drift from first sample {drift:.2f} mm -> "
          f"{'PASS' if worst <= args.tolerance_mm else 'FAIL'}")


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    rig.add_rig_args(parser)
    parser.add_argument("--tape-w-mm", type=float, help="ruler distance TL->TR tape point")
    parser.add_argument("--tape-h-mm", type=float, help="ruler distance TL->BL tape point")
    parser.add_argument("--reuse-marks", action="store_true", help=f"load {MARKS_PATH.name}")
    parser.add_argument("--tolerance-mm", type=float, default=5.0)
    parser.add_argument("--fill", type=int, default=255, help="rectangle gray level 0-255")
    parser.add_argument("--minutes", type=float, default=10.0, help="stability run length")
    parser.add_argument("--interval", type=float, default=30.0, help="seconds between stability samples")
    parser.add_argument("--monitor", action="store_true", help="start the stability run immediately")
    args = parser.parse_args()
    if rig.handle_list(args):
        return
    if not args.reuse_marks and (args.tape_w_mm is None or args.tape_h_mm is None):
        parser.error("--tape-w-mm and --tape-h-mm are required unless --reuse-marks")

    proj = rig.Projector(rig.pick_display(args.display))
    cap = rig.open_camera(args.camera, args.cam_res)
    H = rig.load_homography(proj, cap)
    cam_size = rig.camera_size(cap)

    def click_new_marks():
        frame = rig.blank_and_capture(proj, cap, settle_ms=args.settle_ms)
        tape_cam = click_tape_marks(frame)
        save_marks(tape_cam, args.tape_w_mm, args.tape_h_mm, cam_size)
        return tape_cam

    if args.reuse_marks:
        tape_cam, w_mm, h_mm = load_marks(cam_size)
        args.tape_w_mm = args.tape_w_mm or w_mm     # CLI values override the saved ones
        args.tape_h_mm = args.tape_h_mm or h_mm
    else:
        tape_cam = click_new_marks()

    check = Check(proj, cap, H, tape_cam, args.tape_w_mm, args.tape_h_mm, args)
    key = ord("m") if args.monitor else 32
    while key != 27:
        if key == 32:
            err, disp = check.measure()
            if err is None:
                print("Projected rectangle not found - check exposure (S) or --fill.")
                disp = rig.fit_preview(disp)[0]
            else:
                print_errors(err, args.tolerance_mm)
            cv2.imshow("camera", disp)
        elif key in (ord("m"), ord("M")):
            monitor(check, args)
        elif key in (ord("r"), ord("R")):
            check = Check(proj, cap, H, click_new_marks(), args.tape_w_mm, args.tape_h_mm, args)
            key = 32
            continue
        elif key in (ord("s"), ord("S")):
            rig.open_camera_settings(cap)
        key = cv2.waitKey(0) & 0xFF

    cap.release()
    cv2.destroyAllWindows()


if __name__ == "__main__":
    main()
