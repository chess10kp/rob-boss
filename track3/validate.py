from __future__ import annotations

import argparse
import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Sequence

import numpy as np
from PIL import Image

from track3.components import component_sizes

EXPECTED_MASKS = (
    "01_darkest.png",
    "02_dark.png",
    "03_mid.png",
    "04_light.png",
    "05_lightest.png",
)
MIN_COVERAGE = 0.04
MIN_COMPONENT_FRACTION = 0.001
MAX_PAINTABLE_COMPONENTS = 12
TOP_COMPONENT_COUNT = 10
MIN_TOP_COMPONENT_COVERAGE = 0.90


@dataclass(frozen=True)
class MaskSummary:
    path: str
    coverage: float
    component_count: int
    small_component_count: int
    paintable_component_count: int
    largest_10_area_fraction: float


@dataclass(frozen=True)
class ValidationReport:
    width: int
    height: int
    masks: tuple[MaskSummary, ...]
    gap_pixels: int
    overlap_pixels: int
    errors: tuple[str, ...]
    paintability_errors: tuple[str, ...]

    @property
    def passed(self) -> bool:
        return not self.errors

    @property
    def paintable(self) -> bool:
        return not self.paintability_errors

    def to_dict(self) -> dict[str, object]:
        result = asdict(self)
        result["passed"] = self.passed
        result["paintable"] = self.paintable
        return result




def validate_scene(scene_dir: Path) -> ValidationReport:
    layers_dir = scene_dir / "layers"
    errors: list[str] = []
    paintability_errors: list[str] = []
    summaries: list[MaskSummary] = []
    foregrounds: list[np.ndarray] = []
    dimensions: tuple[int, int] | None = None

    if not layers_dir.is_dir():
        return ValidationReport(
            width=0,
            height=0,
            masks=(),
            gap_pixels=0,
            overlap_pixels=0,
            errors=(f"missing layers directory: {layers_dir}",),
            paintability_errors=(),
        )

    png_names = {path.name for path in layers_dir.glob("*.png")}
    expected_names = set(EXPECTED_MASKS)
    missing = sorted(expected_names - png_names)
    extra = sorted(png_names - expected_names)
    if missing:
        errors.append(f"missing masks: {', '.join(missing)}")
    if extra:
        errors.append(f"unexpected masks: {', '.join(extra)}")

    for name in EXPECTED_MASKS:
        path = layers_dir / name
        if not path.is_file():
            continue

        with Image.open(path) as image:
            if image.format != "PNG":
                errors.append(f"{name}: expected PNG, found {image.format or 'unknown'}")
            if image.mode != "L":
                errors.append(f"{name}: expected 8-bit grayscale mode L, found {image.mode}")
                continue

            current_dimensions = image.size
            if dimensions is None:
                dimensions = current_dimensions
            elif current_dimensions != dimensions:
                errors.append(
                    f"{name}: dimensions {current_dimensions} do not match {dimensions}"
                )
                continue

            pixels = np.asarray(image)

        values = np.unique(pixels)
        invalid_values = values[(values != 0) & (values != 255)]
        if invalid_values.size:
            preview = ", ".join(str(int(value)) for value in invalid_values[:8])
            errors.append(f"{name}: non-binary pixel values: {preview}")

        foreground = pixels == 255
        foreground_pixels = int(np.count_nonzero(foreground))
        coverage = float(foreground_pixels / foreground.size)
        component_sizes_for_mask = sorted(component_sizes(foreground), reverse=True)
        minimum_component_area = max(
            1, int(np.ceil(foreground.size * MIN_COMPONENT_FRACTION))
        )
        small_component_count = sum(
            size < minimum_component_area for size in component_sizes_for_mask
        )
        paintable_component_count = (
            len(component_sizes_for_mask) - small_component_count
        )
        largest_10_area_fraction = (
            float(
                sum(component_sizes_for_mask[:TOP_COMPONENT_COUNT])
                / foreground_pixels
            )
            if foreground_pixels
            else 0.0
        )
        summaries.append(
            MaskSummary(
                path=f"layers/{name}",
                coverage=coverage,
                component_count=len(component_sizes_for_mask),
                small_component_count=small_component_count,
                paintable_component_count=paintable_component_count,
                largest_10_area_fraction=largest_10_area_fraction,
            )
        )
        foregrounds.append(foreground)
        if coverage < MIN_COVERAGE:
            errors.append(
                f"{name}: coverage {coverage:.6f} is below {MIN_COVERAGE:.2%}"
            )
        if small_component_count:
            paintability_errors.append(
                f"{name}: {small_component_count} components are smaller than "
                f"{MIN_COMPONENT_FRACTION:.2%} of the canvas"
            )
        if paintable_component_count > MAX_PAINTABLE_COMPONENTS:
            paintability_errors.append(
                f"{name}: {paintable_component_count} paintable components exceed "
                f"the limit of {MAX_PAINTABLE_COMPONENTS}"
            )
        if largest_10_area_fraction < MIN_TOP_COMPONENT_COVERAGE:
            paintability_errors.append(
                f"{name}: largest {TOP_COMPONENT_COUNT} components cover "
                f"{largest_10_area_fraction:.2%}, below "
                f"{MIN_TOP_COMPONENT_COVERAGE:.0%}"
            )

    gap_pixels = 0
    overlap_pixels = 0
    if len(foregrounds) == len(EXPECTED_MASKS):
        memberships = np.sum(np.stack(foregrounds, axis=0), axis=0)
        gap_pixels = int(np.count_nonzero(memberships == 0))
        overlap_pixels = int(np.count_nonzero(memberships > 1))
        if gap_pixels:
            errors.append(f"partition has {gap_pixels} uncovered pixels")
        if overlap_pixels:
            errors.append(f"partition has {overlap_pixels} overlapping pixels")

    width, height = dimensions or (0, 0)
    return ValidationReport(
        width=width,
        height=height,
        masks=tuple(summaries),
        gap_pixels=gap_pixels,
        overlap_pixels=overlap_pixels,
        errors=tuple(errors),
        paintability_errors=tuple(paintability_errors),
    )


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Validate a Spike C five-mask scene directory."
    )
    parser.add_argument("scene_dir", type=Path)
    args = parser.parse_args(argv)

    report = validate_scene(args.scene_dir)
    print(json.dumps(report.to_dict(), indent=2))
    return 0 if report.passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
