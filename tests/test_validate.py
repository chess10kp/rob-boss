from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import numpy as np
from PIL import Image

from track3.validate import EXPECTED_MASKS, validate_scene


class ValidateSceneTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.scene_dir = Path(self.temporary_directory.name)
        (self.scene_dir / "layers").mkdir()

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def write_labels(self, labels: np.ndarray) -> None:
        for index, name in enumerate(EXPECTED_MASKS):
            mask = np.where(labels == index, 255, 0).astype(np.uint8)
            Image.fromarray(mask, mode="L").save(self.scene_dir / "layers" / name)

    def test_accepts_complete_disjoint_partition(self) -> None:
        labels = np.tile(np.arange(5, dtype=np.uint8), (10, 2)).reshape(10, 10)
        self.write_labels(labels)

        report = validate_scene(self.scene_dir)

        self.assertTrue(report.passed, report.errors)
        self.assertEqual(report.gap_pixels, 0)
        self.assertEqual(report.overlap_pixels, 0)
        self.assertEqual([mask.coverage for mask in report.masks], [0.2] * 5)

        self.assertTrue(report.paintable, report.paintability_errors)

    def test_rejects_gap(self) -> None:
        labels = np.tile(np.arange(5, dtype=np.uint8), (10, 2)).reshape(10, 10)
        self.write_labels(labels)
        path = self.scene_dir / "layers" / EXPECTED_MASKS[0]
        pixels = np.asarray(Image.open(path)).copy()
        pixels[0, 0] = 0
        Image.fromarray(pixels, mode="L").save(path)

        report = validate_scene(self.scene_dir)

        self.assertFalse(report.passed)
        self.assertEqual(report.gap_pixels, 1)
        self.assertEqual(report.overlap_pixels, 0)

    def test_rejects_overlap(self) -> None:
        labels = np.tile(np.arange(5, dtype=np.uint8), (10, 2)).reshape(10, 10)
        self.write_labels(labels)
        path = self.scene_dir / "layers" / EXPECTED_MASKS[1]
        pixels = np.asarray(Image.open(path)).copy()
        pixels[0, 0] = 255
        Image.fromarray(pixels, mode="L").save(path)

        report = validate_scene(self.scene_dir)

        self.assertFalse(report.passed)
        self.assertEqual(report.gap_pixels, 0)
        self.assertEqual(report.overlap_pixels, 1)

    def test_rejects_mask_below_four_percent(self) -> None:
        labels = np.array([0] * 3 + [1] * 20 + [2] * 20 + [3] * 20 + [4] * 37)
        self.write_labels(labels.reshape(10, 10))

        report = validate_scene(self.scene_dir)

        self.assertFalse(report.passed)
        self.assertEqual(report.gap_pixels, 0)
        self.assertEqual(report.overlap_pixels, 0)
        self.assertIn("coverage 0.030000 is below 4.00%", "\n".join(report.errors))

    def test_reports_confetti_separately_from_contract_failure(self) -> None:
        labels = np.repeat(np.arange(5, dtype=np.uint8), 20)
        labels = np.tile(labels, (100, 1))
        for index in range(20):
            labels[2 + index * 4, 25] = 0
        self.write_labels(labels)

        report = validate_scene(self.scene_dir)

        self.assertTrue(report.passed, report.errors)
        self.assertFalse(report.paintable)
        self.assertEqual(report.masks[0].small_component_count, 20)
        self.assertEqual(report.masks[0].paintable_component_count, 1)


if __name__ == "__main__":
    unittest.main()
