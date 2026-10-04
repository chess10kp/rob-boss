"""Camera <-> projector <-> canvas-space geometry. Pure functions, no hardware.

Canvas space (PLAN.md): normalized 0-1 within the detected canvas quad, origin top-left.
Quads are float32 (4, 2) arrays ordered TL, TR, BR, BL.
"""

from __future__ import annotations

import numpy as np
import cv2


def scale_homography(H, solved_cam_size, cam_size):
    """H maps camera px -> projector px at solved_cam_size; return the same map for frames
    of cam_size. Valid only when both modes see the same field of view (same aspect)."""
    sw, sh = solved_cam_size
    w, h = cam_size
    if abs(sw / sh - w / h) > 0.01:
        raise ValueError(f"homography solved at {sw}x{sh} can't be rescaled to {w}x{h} (aspect differs)")
    S = np.diag([sw / w, sh / h, 1.0])          # new camera px -> solved camera px
    return H @ S


def order_quad(pts):
    """Order 4 points TL, TR, BR, BL (image coordinates, y down)."""
    pts = np.asarray(pts, np.float32).reshape(4, 2)
    s, d = pts.sum(1), pts[:, 0] - pts[:, 1]
    return np.float32([pts[s.argmin()], pts[d.argmax()], pts[s.argmax()], pts[d.argmin()]])


def transform(points, H):
    return cv2.perspectiveTransform(np.float32(points).reshape(-1, 1, 2), H).reshape(-1, 2)


def detect_canvas_quad(frame, cam_to_proj, proj_size, aspect=None, aspect_tol=0.12,
                       min_frac=0.03, max_frac=0.9, edge_margin=25):
    """Largest convex quadrilateral in the frame that sits inside the projected area.

    `frame` should be lit evenly (a flash capture). The projected area's own outline is
    also a big bright quad, so candidates are judged in projector space and any that
    touch the projector's edge (within edge_margin px) are rejected. `aspect` (long side /
    short side, e.g. 11 / 8.5 for letter paper) rejects quads of the wrong shape.
    Returns the quad in camera px, or None.
    """
    gray = cv2.GaussianBlur(cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY), (5, 5), 0)
    edges = cv2.Canny(gray, 20, 60)
    pw, ph = proj_size

    def score(quad):
        """Area fraction of an acceptable quad, else 0."""
        qp = transform(quad, cam_to_proj)
        area = cv2.contourArea(qp) / (pw * ph)
        inside = ((qp[:, 0] > edge_margin) & (qp[:, 0] < pw - edge_margin)
                  & (qp[:, 1] > edge_margin) & (qp[:, 1] < ph - edge_margin)).all()
        w, h = quad_size(qp)
        shaped = aspect is None or abs(max(w, h) / min(w, h) / aspect - 1) <= aspect_tol
        return area if (min_frac <= area <= max_frac and inside and shaped) else 0.0

    # 1. a closed four-sided contour (clean, continuous edges)
    contours, _ = cv2.findContours(cv2.dilate(edges, np.ones((3, 3), np.uint8)), cv2.RETR_LIST,
                                   cv2.CHAIN_APPROX_SIMPLE)
    best, best_area = None, 0.0
    for c in contours:
        approx = cv2.approxPolyDP(c, 0.02 * cv2.arcLength(c, True), True)
        if len(approx) != 4 or not cv2.isContourConvex(approx):
            continue
        quad = order_quad(approx)
        area = score(quad)
        if area > best_area:
            best, best_area = quad, area
    if best is not None:
        return best

    # 2. faint, broken edges (white paper on the white mat): keep only edges well inside
    #    the projected area, bridge the gaps, and take the outer hull of the biggest
    #    edge cluster as the sheet.
    inner = np.zeros((ph, pw), np.uint8)
    inner[edge_margin:ph - edge_margin, edge_margin:pw - edge_margin] = 255
    inner = cv2.warpPerspective(inner, np.linalg.inv(cam_to_proj), (frame.shape[1], frame.shape[0]))
    closed = cv2.morphologyEx(edges & cv2.erode(inner, np.ones((9, 9), np.uint8)),
                              cv2.MORPH_CLOSE, np.ones((9, 9), np.uint8), iterations=2)
    contours, _ = cv2.findContours(closed, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if not contours:
        return None
    hull = cv2.convexHull(max(contours, key=lambda c: cv2.contourArea(cv2.convexHull(c))))
    for eps in (0.01, 0.02, 0.03, 0.05, 0.08):
        approx = cv2.approxPolyDP(hull, eps * cv2.arcLength(hull, True), True)
        if len(approx) == 4:
            quad = order_quad(approx)
            return quad if score(quad) else None
    return None


def quad_size(quad):
    """(width, height) of a quad: mean of opposite side lengths."""
    tl, tr, br, bl = quad
    w = (np.linalg.norm(tr - tl) + np.linalg.norm(br - bl)) / 2
    h = (np.linalg.norm(bl - tl) + np.linalg.norm(br - tr)) / 2
    return float(w), float(h)


def canvas_pixels(quad_proj, long_side=None):
    """Pixel size for a canvas-space image of this canvas. The projector has square,
    near-uniform pixels on the mat, so the projector-space quad gives the true aspect.
    Default resolution matches the canvas's footprint in projector px."""
    w, h = quad_size(quad_proj)
    if long_side:
        k = long_side / max(w, h)
        w, h = w * k, h * k
    return max(1, round(w)), max(1, round(h))


def rect_corners(w, h):
    return np.float32([[0, 0], [w, 0], [w, h], [0, h]])


def rectify(frame, quad_cam, size):
    """Warp the canvas region of a camera frame to a (w, h) canvas-space image."""
    w, h = size
    M = cv2.getPerspectiveTransform(np.float32(quad_cam), rect_corners(w, h))
    return cv2.warpPerspective(frame, M, (w, h), flags=cv2.INTER_LINEAR)


def canvas_to_projector(img, quad_proj, proj_size):
    """Warp a canvas-space image onto the canvas's place in the projector frame (black elsewhere)."""
    h, w = img.shape[:2]
    M = cv2.getPerspectiveTransform(rect_corners(w, h), np.float32(quad_proj))
    return cv2.warpPerspective(img, M, tuple(proj_size), flags=cv2.INTER_LINEAR,
                               borderMode=cv2.BORDER_CONSTANT, borderValue=0)
