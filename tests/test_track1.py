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


class ColourProfileTest(unittest.TestCase):
    def test_fit_recovers_projector_and_correction_lands_on_target(self):
        from track1.colour import Profile, to_linear, to_srgb
        rng = np.random.default_rng(1)
        sent = rng.uniform(20, 245, (40, 3))
        # short of red; cross terms kept positive so no sent colour asks for negative light
        M = np.array([[0.7, 0.05, 0.02], [0.02, 1.0, 0.05], [0.01, 0.05, 0.95]])
        b = np.array([0.03, 0.0, 0.0])
        paper = lambda s: to_srgb(to_linear(s) @ M.T + b)                         # simulated projector
        p = Profile.fit(sent, paper(sent))
        np.testing.assert_allclose(p.M, M, atol=1e-3)
        want = np.array([[200.0, 120.0, 60.0], [90.0, 140.0, 200.0]])
        got = paper(p.drive(want))
        np.testing.assert_allclose(to_linear(got), p.k * to_linear(want), atol=2e-3)
        self.assertLess(p.k, 1.0)                                                 # white needs dimming

    def test_correct_keeps_black_black(self):
        from track1.colour import Profile
        p = Profile(np.eye(3), np.array([0.02, 0, 0]), 0.8)
        img = np.zeros((4, 4, 3), np.uint8)
        img[0, 0] = (50, 100, 200)
        out = p.correct(img)
        self.assertEqual(out[3, 3].sum(), 0)
        self.assertGreater(out[0, 0].sum(), 0)


class MaskedStepTest(unittest.TestCase):
    def test_step_is_compiled_frame_masked_by_its_own_layer(self):
        """canvas = canvas * (1 - alpha) + colour * alpha builds the main image; each step
        then lights only its own layer's area of it, with opacity divided out of the mask."""
        import tempfile
        from pathlib import Path
        from track1.project_scene import compiled_frames, masked_step
        with tempfile.TemporaryDirectory() as d:
            under = np.zeros((10, 10, 4), np.uint8)
            under[...] = (255, 0, 0, 255)                                 # layer 1: opaque blue (BGRA)
            over = np.zeros((10, 10, 4), np.uint8)
            over[...] = (0, 0, 255, 0)                                    # layer 2: red at opacity 0.5 ...
            over[:, :5, 3] = 128                                          # ... on the left half only
            paths = [Path(d) / "1.png", Path(d) / "2.png"]
            cv2.imwrite(str(paths[0]), under)
            cv2.imwrite(str(paths[1]), over)
            frames = compiled_frames(paths, bare=245)
            step2 = masked_step(frames[1], paths[1], opacity=0.5)
        a = 128 / 255
        # inside layer 2: the compiled colour (red over blue at 50%), at full brightness
        np.testing.assert_allclose(step2[0, 0], np.round([255 * (1 - a), 0, 255 * a]), atol=1)
        self.assertEqual(step2[0, 9].sum(), 0)                            # outside layer 2: dark


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

    def test_outline_mask_outlines_visible_part_while_fill_covers_whole_layer(self):
        visible = np.zeros_like(self.mask)
        visible[100:300, 250:400] = 255                                 # right part of the filled region
        img = overlay.render(self.mask, {"fill_rgb": (0, 0, 255), "fill_alpha": 1.0,
                                         "outline_rgb": (0, 255, 0), "outline_px": 3,
                                         "outline_mask": visible})
        self.assertEqual(tuple(img[200, 150]), (255, 0, 0))            # buried part is still filled
        self.assertEqual(tuple(img[200, 250]), (0, 255, 0))            # outline runs along the visible part
        self.assertNotEqual(tuple(img[200, 100]), (0, 255, 0))         # not along the filled region's edge

    def test_render_resizes_mask(self):
        img = overlay.render(self.mask, size=(300, 200))
        self.assertEqual(img.shape, (200, 300, 3))


if __name__ == "__main__":
    unittest.main()
