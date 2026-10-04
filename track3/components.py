from __future__ import annotations

from collections.abc import Iterator

import numpy as np


def iter_components(mask: np.ndarray) -> Iterator[list[int]]:
    if mask.ndim != 2:
        raise ValueError("component masks must be two-dimensional")

    height, width = mask.shape
    visited = np.zeros(mask.shape, dtype=bool)
    for y in range(height):
        for x_value in np.flatnonzero(mask[y]):
            x = int(x_value)
            if visited[y, x]:
                continue
            visited[y, x] = True
            stack = [y * width + x]
            component: list[int] = []
            while stack:
                position = stack.pop()
                component.append(position)
                current_y, current_x = divmod(position, width)
                for neighbor_y in range(
                    max(0, current_y - 1), min(height, current_y + 2)
                ):
                    for neighbor_x in range(
                        max(0, current_x - 1), min(width, current_x + 2)
                    ):
                        if (
                            mask[neighbor_y, neighbor_x]
                            and not visited[neighbor_y, neighbor_x]
                        ):
                            visited[neighbor_y, neighbor_x] = True
                            stack.append(neighbor_y * width + neighbor_x)
            yield component


def component_sizes(mask: np.ndarray) -> list[int]:
    return [len(component) for component in iter_components(mask)]


def clean_small_components(
    labels: np.ndarray,
    *,
    label_count: int,
    minimum_area: int,
    max_passes: int = 8,
) -> tuple[np.ndarray, dict[str, int]]:
    if labels.ndim != 2:
        raise ValueError("label maps must be two-dimensional")
    if minimum_area < 1:
        raise ValueError("minimum_area must be positive")

    cleaned = labels.copy()
    height, width = cleaned.shape
    reassigned_pixels = 0
    completed_passes = 0

    for pass_index in range(max_passes):
        changed = False
        for label in range(label_count):
            label_snapshot = cleaned == label
            for component in iter_components(label_snapshot):
                if len(component) >= minimum_area:
                    continue

                neighbor_counts = np.zeros(label_count, dtype=np.int64)
                for position in component:
                    y, x = divmod(position, width)
                    if y > 0:
                        neighbor_counts[int(cleaned[y - 1, x])] += 1
                    if y + 1 < height:
                        neighbor_counts[int(cleaned[y + 1, x])] += 1
                    if x > 0:
                        neighbor_counts[int(cleaned[y, x - 1])] += 1
                    if x + 1 < width:
                        neighbor_counts[int(cleaned[y, x + 1])] += 1
                neighbor_counts[label] = 0
                longest_boundary = int(neighbor_counts.max())
                if longest_boundary == 0:
                    continue

                candidates = np.flatnonzero(neighbor_counts == longest_boundary)
                replacement = min(
                    (int(candidate) for candidate in candidates),
                    key=lambda candidate: (abs(candidate - label), candidate),
                )
                cleaned.reshape(-1)[np.asarray(component, dtype=np.intp)] = replacement
                reassigned_pixels += len(component)
                changed = True

        completed_passes = pass_index + 1
        if not changed:
            break

    return cleaned, {
        "minimum_component_area": minimum_area,
        "passes": completed_passes,
        "reassigned_pixels": reassigned_pixels,
    }
