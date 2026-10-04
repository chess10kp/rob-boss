from __future__ import annotations

import unittest

import numpy as np

from track3.components import clean_small_components, component_sizes


class CleanSmallComponentsTest(unittest.TestCase):
    def test_reassigns_islands_to_longest_adjacent_boundary(self) -> None:
        labels = np.repeat(np.arange(5, dtype=np.uint8), 4)
        labels = np.tile(labels, (20, 1))
        labels[5, 6] = 0
        labels[10, 6] = 0

        cleaned, report = clean_small_components(
            labels,
            label_count=5,
            minimum_area=3,
        )

        self.assertEqual(int(cleaned[5, 6]), 1)
        self.assertEqual(int(cleaned[10, 6]), 1)
        self.assertEqual(report["reassigned_pixels"], 2)
        self.assertEqual(set(np.unique(cleaned)), {0, 1, 2, 3, 4})
        for label in range(5):
            self.assertTrue(
                all(size >= 3 for size in component_sizes(cleaned == label))
            )


if __name__ == "__main__":
    unittest.main()
