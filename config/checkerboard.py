"""
Camera -> projector homography via a projected checkerboard.  (Spike A, part 1)

The projector can't "see", so we project a checkerboard whose inner-corner
positions we know exactly in projector pixels, detect those corners in the
overhead camera image, and fit a homography between the two point sets.

Result: H maps camera pixels -> projector pixels, valid for the work-surface plane.
Project onto the canvas itself (not the mat beside it): the homography only holds
at the height it was solved at, and a canvas board is several mm thick.

Usage:
    uv run config/checkerboard.py --list
    uv run config/checkerboard.py --camera 1 --cam-res 3840x2160
Keys:  SPACE = capture/solve,  S = camera settings dialog,  ESC = quit
Then run corner_check.py to measure the error at the tape marks.
"""

import argparse

import cv2
import numpy as np

import rig

INNER_COLS, INNER_ROWS = 9, 6      # inner corners; one even + one odd avoids 180° ambiguity
UNDISTORT = False                  # set True once you have camera intrinsics (K, dist)
K, DIST = None, None               # e.g. loaded from a prior cv2.calibrateCamera run


def make_checkerboard(proj_w, proj_h, square_px):
    """Checkerboard centered on a white projector frame + its inner-corner coords."""
    sq_cols, sq_rows = INNER_COLS + 1, INNER_ROWS + 1
    board_w, board_h = sq_cols * square_px, sq_rows * square_px
    if board_w > proj_w or board_h > proj_h:
        raise SystemExit(f"board {board_w}x{board_h} doesn't fit projector {proj_w}x{proj_h}; "
                         "lower --square-px")
    x0, y0 = (proj_w - board_w) // 2, (proj_h - board_h) // 2

    img = np.full((proj_h, proj_w), 255, np.uint8)  # white border helps detection
    for r in range(sq_rows):
        for c in range(sq_cols):
            if (r + c) % 2 == 0:
                img[y0 + r * square_px:y0 + (r + 1) * square_px,
                    x0 + c * square_px:x0 + (c + 1) * square_px] = 0

    # inner corners, row-major, same order OpenCV reports them
    corners = np.array([[x0 + (c + 1) * square_px, y0 + (r + 1) * square_px]
                        for r in range(INNER_ROWS) for c in range(INNER_COLS)],
                       np.float32)
    return cv2.cvtColor(img, cv2.COLOR_GRAY2BGR), corners


def grab_frame(cap):
    frame = rig.grab_frame(cap)
    if UNDISTORT and K is not None:
        frame = cv2.undistort(frame, K, DIST)
    return frame


def detect_corners(frame):
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    found, pts = cv2.findChessboardCornersSB(
        gray, (INNER_COLS, INNER_ROWS),
        flags=cv2.CALIB_CB_EXHAUSTIVE | cv2.CALIB_CB_ACCURACY)
    return (pts.reshape(-1, 2) if found else None), gray


def fix_ordering(gray, cam_pts, proj_pts, square_px):
    """Guard against the detector returning corners in reversed order.

    The inner-corner grid is 180°-symmetric, so both orders fit a homography equally
    well (and the verification circles land on corners either way). Only the square
    colours break the symmetry: keep the order whose H puts the board's top-left
    square (black in make_checkerboard) on a darker camera patch than its white neighbour.
    """
    x, y = proj_pts[0] - square_px / 2                    # centre of the top-left (black) square
    probe = np.array([[[x, y]], [[x + square_px, y]]], np.float32)   # black, then white
    best = None
    for cand in (cam_pts, cam_pts[::-1].copy()):
        H, _ = cv2.findHomography(cand, proj_pts)
        (bx, by), (wx, wy) = cv2.perspectiveTransform(probe, np.linalg.inv(H)).reshape(-1, 2)
        contrast = float(gray[int(wy), int(wx)]) - float(gray[int(by), int(bx)])
        if best is None or contrast > best[0]:
            best = (contrast, cand)
    return best[1]


def reproj_error(H, cam_pts, proj_pts):
    mapped = cv2.perspectiveTransform(cam_pts.reshape(-1, 1, 2), H).reshape(-1, 2)
    return float(np.sqrt(((mapped - proj_pts) ** 2).sum(axis=1)).mean())


def verify(H, cam_pts, proj_w, proj_h):
    """Project a circle where each detected corner should be; they should sit on the corners."""
    canvas = np.zeros((proj_h, proj_w, 3), np.uint8)
    mapped = cv2.perspectiveTransform(cam_pts.reshape(-1, 1, 2), H).reshape(-1, 2)
    for x, y in mapped:
        cv2.circle(canvas, (int(round(x)), int(round(y))), 10, (0, 255, 0), 2)
    return canvas


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    rig.add_rig_args(parser)
    parser.add_argument("--square-px", type=int, default=90,
                        help="checkerboard square size in projector pixels")
    args = parser.parse_args()
    if rig.handle_list(args):
        return

    proj = rig.Projector(rig.pick_display(args.display))
    board, proj_pts = make_checkerboard(proj.w, proj.h, args.square_px)
    cap = rig.open_camera(args.camera, args.cam_res)

    H = None
    while True:
        proj.show(board if H is None else verify(H, last_cam_pts, proj.w, proj.h))
        ok, live = cap.read()                    # preview only: no drain, stays responsive
        if ok:
            cv2.imshow("camera", rig.fit_preview(live)[0])
        key = cv2.waitKey(30) & 0xFF

        if key == 27:
            break
        if key in (ord("s"), ord("S")):
            rig.open_camera_settings(cap)
        if key == 32:  # SPACE
            proj.show(board, args.settle_ms)     # let the projection settle
            cam_pts, gray = detect_corners(grab_frame(cap))
            if cam_pts is None:
                print("Checkerboard not found - adjust exposure/focus/board size.")
                continue
            cam_pts = fix_ordering(gray, cam_pts, proj_pts, args.square_px)
            H, inliers = cv2.findHomography(cam_pts, proj_pts, cv2.RANSAC, 2.0)
            err = reproj_error(H, cam_pts, proj_pts)
            print(f"Homography solved. Mean reprojection error: {err:.2f} projector px "
                  f"({int(inliers.sum())}/{len(cam_pts)} inliers)")
            np.savez(rig.HOMOGRAPHY_PATH, H=H, H_inv=np.linalg.inv(H),
                     cam_pts=cam_pts, proj_pts=proj_pts,
                     proj_size=(proj.w, proj.h), cam_size=rig.camera_size(cap))
            last_cam_pts = cam_pts
            print(f"Saved {rig.HOMOGRAPHY_PATH.name} - projector now shows verification circles.")

    cap.release()
    cv2.destroyAllWindows()


if __name__ == "__main__":
    main()
