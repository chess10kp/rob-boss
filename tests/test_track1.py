from __future__ import annotations

import unittest

import cv2
import numpy as np

from track1 import geometry, overlay

PROJ = (1920, 1280)


def toy_homography():
    """A mild perspective map from a 1104x828 camera to the 1920x1280 projector."""
    cam = np.float32([[80, 120], [1000, 110], [960, 700], [120, 690]])
    return cv2.getPerspectiveTransform(cam, geometry.rect_corners(*PROJ))


class GeometryTest(unittest.TestCase):
    def test_scale_homography_maps_same_physical_point(self):
        H = toy_homography()
        H2 = geometry.scale_homography(H, (1104, 828), (2208, 1656))
        p = np.float32([[500, 400]])
        np.testing.assert_allclose(geometry.transform(p * 2, H2), geometry.transform(p, H), atol=1e-3)

    def test_scale_homography_rejects_different_aspect(self):
        with self.assertRaises(ValueError):
            geometry.scale_homography(toy_homography(), (1104, 828), (1920, 1080))

    def test_order_quad(self):
        q = geometry.order_quad([[10, 90], [90, 10], [10, 10], [90, 90]])
        np.testing.assert_array_equal(q, [[10, 10], [90, 10], [90, 90], [10, 90]])

    def test_canvas_round_trip(self):
        """A mask projected onto the canvas and captured back lands where it started."""
        H = toy_homography()
        quad_cam = np.float32([[300, 250], [700, 240], [720, 560], [290, 570]])
        quad_proj = geometry.transform(quad_cam, H)
        size = geometry.canvas_pixels(quad_proj)
        mask = np.zeros(size[::-1], np.uint8)
        mask[:, : size[0] // 3] = 255                                   # left third
        projected = geometry.canvas_to_projector(mask, quad_proj, PROJ)
        camera = cv2.warpPerspective(projected, np.linalg.inv(H), (1104, 828))   # what the camera sees
        back = geometry.rectify(camera, quad_cam, size)
        iou = ((back > 127) & (mask > 127)).sum() / ((back > 127) | (mask > 127)).sum()
        self.assertGreater(iou, 0.97)

    def test_detect_canvas_ignores_projection_outline(self):
        """The bright projected area is itself a quad; only the canvas inside it counts."""
        H = toy_homography()
        frame = np.full((828, 1104, 3), 20, np.uint8)
        lit = cv2.warpPerspective(np.full((PROJ[1], PROJ[0]), 180, np.uint8), np.linalg.inv(H), (1104, 828))
        frame[lit > 0] = 180
        canvas = np.int32([[350, 280], [650, 270], [670, 520], [340, 530]])
        cv2.fillPoly(frame, [canvas], (235, 235, 235))
        quad = geometry.detect_canvas_quad(frame, H, PROJ)
        self.assertIsNotNone(quad)
        self.assertLess(np.abs(quad - canvas).max(), 4)

    def lit_frame(self, H):
        lit = cv2.warpPerspective(np.full((PROJ[1], PROJ[0]), 180, np.uint8), np.linalg.inv(H), (1104, 828))
        frame = np.full((828, 1104, 3), 20, np.uint8)
        frame[lit > 0] = 180
        return frame

    def test_detect_paper_with_broken_faint_edges(self):
        """White paper on the white mat: the outline shows only as faint, dashed edges."""
        H = toy_homography()
        frame = self.lit_frame(H)
        sheet = geometry.transform(np.float32([[620, 230], [1300, 230], [1300, 1110], [620, 1110]]),
                                   np.linalg.inv(H))                          # letter-shaped in projector px
        for a, b in zip(sheet, np.roll(sheet, -1, 0)):
            for t in np.arange(0, 1, 0.1):                                    # dashes with ~13 px gaps
                p, q = a + (b - a) * t, a + (b - a) * min(1, t + 0.06)
                cv2.line(frame, tuple(int(v) for v in p), tuple(int(v) for v in q), (140, 140, 140), 2)
        quad = geometry.detect_canvas_quad(frame, H, PROJ, aspect=11 / 8.5)
        self.assertIsNotNone(quad)
        self.assertLess(np.abs(quad - sheet).max(), 6)

    def test_detect_rejects_wrong_aspect(self):
        H = toy_homography()
        frame = self.lit_frame(H)
        square = geometry.transform(np.float32([[660, 340], [1260, 340], [1260, 940], [660, 940]]),
                                    np.linalg.inv(H))
        cv2.fillPoly(frame, [np.int32(square)], (235, 235, 235))
        self.assertIsNone(geometry.detect_canvas_quad(frame, H, PROJ, aspect=11 / 8.5))
        self.assertIsNotNone(geometry.detect_canvas_quad(frame, H, PROJ))

    def test_detect_canvas_none_when_empty(self):
        H = toy_homography()
        frame = cv2.warpPerspective(np.full((PROJ[1], PROJ[0], 3), 180, np.uint8), np.linalg.inv(H), (1104, 828))
        self.assertIsNone(geometry.detect_canvas_quad(frame, H, PROJ))


class OverlayTest(unittest.TestCase):
    def setUp(self):
        self.mask = np.zeros((400, 600), np.uint8)
        self.mask[100:300, 100:400] = 255

    def test_fill_only_inside_region(self):
        img = overlay.render(self.mask, {"fill_rgb": (0, 0, 255), "fill_alpha": 1.0, "outline": False})
        self.assertEqual(tuple(img[200, 250]), (255, 0, 0))            # BGR blue inside
        self.assertEqual(img[50, 50].sum(), 0)                          # black outside

    def test_arrows_stay_inside_region_and_point_along_direction(self):
        img = overlay.render(self.mask, {"fill": False, "outline": False, "arrows": True,
                                         "stroke_dir_deg": 0, "arrow_spacing_px": 80, "arrow_px": 2})
        lit = img.sum(2) > 0
        self.assertTrue(lit.any())
        self.assertFalse(lit[~(cv2.dilate(self.mask, np.ones((7, 7), np.uint8)) > 0)].any())
        ys, xs = np.nonzero(lit)
        self.assertGreater(np.ptp(xs), np.ptp(ys))                      # horizontal strokes

    def test_fill_image_copies_layer_colours_inside_region(self):
        layer = np.zeros((400, 600, 4), np.uint8)
        layer[:, :300] = (255, 0, 0, 255)                              # BGRA: blue left half
        layer[:, 300:] = (0, 0, 255, 128)                              # red right half, alpha ignored
        img = overlay.render(self.mask, {"fill_image": layer, "fill_alpha": 1.0, "outline": False})
        self.assertEqual(tuple(img[200, 150]), (255, 0, 0))
        self.assertEqual(tuple(img[200, 350]), (0, 0, 255))
        self.assertEqual(img[50, 50].sum(), 0)                          # outside the mask stays dark

    def test_render_resizes_mask(self):
        img = overlay.render(self.mask, size=(300, 200))
        self.assertEqual(img.shape, (200, 300, 3))


if __name__ == "__main__":
    unittest.main()
