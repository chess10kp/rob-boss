from __future__ import annotations

import argparse
import hashlib
import json
import shutil
from pathlib import Path
from typing import Any, Sequence

import numpy as np
from PIL import Image, ImageDraw, ImageOps

from track3.components import clean_small_components

from track3.gimp_mcp_runtime import (
    McpClient,
    default_install_dir,
    gimp_service,
    install_runtime,
)
from track3.validate import MIN_COMPONENT_FRACTION, validate_scene

LEVELS = (
    ("01_darkest.png", "darkest", 0),
    ("02_dark.png", "dark", 64),
    ("03_mid.png", "mid", 127),
    ("04_light.png", "light", 191),
    ("05_lightest.png", "lightest", 255),
)
POSTERIZE_LEVELS = 5
MAX_EDGE = 1024
DENOISE_STRENGTH = 40


def _unwrap(value: Any) -> Any:
    while isinstance(value, dict) and set(value) == {"result"}:
        value = value["result"]
    return value


def _posterize(client: McpClient) -> None:
    code = (
        "image=Gimp.get_images()[0]; "
        "drawable=(image.get_selected_layers() or image.get_layers())[0]; "
        "proc=Gimp.get_pdb().lookup_procedure('gimp-drawable-posterize'); "
        "assert proc is not None, 'gimp-drawable-posterize unavailable'; "
        "cfg=proc.create_config(); "
        "cfg.set_property('drawable', drawable); "
        f"cfg.set_property('levels', {POSTERIZE_LEVELS}); "
        "proc.run(cfg); "
        "Gimp.displays_flush(); "
        "print('posterized')"
    )
    result = client.call(
        "call_api",
        {"api_path": "exec", "args": ["pyGObject-console", [code]]},
    )
    if "Error" in str(result):
        raise RuntimeError(f"GIMP posterize failed: {result}")


def _normalize_exported_mask(path: Path) -> None:
    with Image.open(path) as image:
        if image.format != "PNG":
            raise RuntimeError(f"GIMP exported non-PNG mask: {path}")
        if image.mode in ("RGB", "RGBA"):
            channels = np.asarray(image.convert("RGB"))
            if not np.array_equal(channels[..., 0], channels[..., 1]) or not np.array_equal(
                channels[..., 1], channels[..., 2]
            ):
                raise RuntimeError(f"GIMP mask contains non-grayscale RGB pixels: {path}")
        grayscale = np.asarray(image.convert("L"))

    values = np.unique(grayscale)
    if not np.all(np.isin(values, (0, 255))):
        preview = ", ".join(str(int(value)) for value in values[:12])
        raise RuntimeError(f"GIMP mask is not binary ({preview}): {path}")
    Image.fromarray(grayscale, mode="L").save(path, format="PNG")


def _clean_masks(mask_paths: Sequence[Path]) -> dict[str, int]:
    foregrounds: list[np.ndarray] = []
    for path in mask_paths:
        with Image.open(path) as image:
            foregrounds.append(np.asarray(image) == 255)

    memberships = np.sum(np.stack(foregrounds, axis=0), axis=0)
    gap_pixels = int(np.count_nonzero(memberships == 0))
    overlap_pixels = int(np.count_nonzero(memberships > 1))
    if gap_pixels or overlap_pixels:
        raise RuntimeError(
            "GIMP color separation did not produce a partition: "
            f"{gap_pixels} gaps, {overlap_pixels} overlaps"
        )

    labels = np.argmax(np.stack(foregrounds, axis=0), axis=0).astype(np.uint8)
    minimum_area = max(
        1, int(np.ceil(labels.size * MIN_COMPONENT_FRACTION))
    )
    cleaned, cleanup = clean_small_components(
        labels,
        label_count=len(mask_paths),
        minimum_area=minimum_area,
    )
    for label, path in enumerate(mask_paths):
        pixels = np.where(cleaned == label, 255, 0).astype(np.uint8)
        Image.fromarray(pixels, mode="L").save(path, format="PNG")
    return cleanup


def _average_rgb(reference: np.ndarray, mask: np.ndarray) -> list[int]:
    selected = reference[mask]
    if selected.size == 0:
        return [0, 0, 0]
    return [int(round(value)) for value in selected.mean(axis=0)]


def _contact_sheet(source: Path, posterized: Path, masks: Sequence[Path], output: Path) -> None:
    tile_width = 320
    tile_height = 230
    label_height = 26
    tiles: list[tuple[str, Image.Image]] = []
    with Image.open(source) as image:
        tiles.append(("reference", image.convert("RGB").copy()))
    with Image.open(posterized) as image:
        tiles.append(("5-value posterized", image.convert("RGB").copy()))
    for path in masks:
        with Image.open(path) as image:
            tiles.append((path.stem, image.convert("RGB").copy()))

    columns = 3
    rows = (len(tiles) + columns - 1) // columns
    sheet = Image.new("RGB", (columns * tile_width, rows * tile_height), "#202020")
    draw = ImageDraw.Draw(sheet)
    for index, (label, image) in enumerate(tiles):
        image.thumbnail((tile_width, tile_height - label_height), Image.Resampling.LANCZOS)
        x = (index % columns) * tile_width
        y = (index // columns) * tile_height
        tile = ImageOps.pad(
            image,
            (tile_width, tile_height - label_height),
            color="#202020",
            method=Image.Resampling.LANCZOS,
        )
        sheet.paste(tile, (x, y + label_height))
        draw.text((x + 8, y + 6), label, fill="white")
    sheet.save(output, format="PNG")


def decompose_scene(client: McpClient, source: Path, scene_dir: Path) -> dict[str, Any]:
    source = source.resolve()
    scene_dir = scene_dir.resolve()
    if scene_dir.exists():
        shutil.rmtree(scene_dir)
    layers_dir = scene_dir / "layers"
    layers_dir.mkdir(parents=True)
    trace: list[dict[str, Any]] = []
    opened = False

    def call(name: str, arguments: dict[str, Any] | None = None) -> Any:
        result = _unwrap(client.call(name, arguments))
        trace.append({"tool": name, "arguments": arguments or {}})
        return result

    try:
        opened_result = call("open_image", {"file_path": str(source)})
        opened = True
        if not isinstance(opened_result, dict):
            raise RuntimeError(f"unexpected open_image response: {opened_result!r}")
        call(
            "scale_to_fit",
            {"max_width": MAX_EDGE, "max_height": MAX_EDGE, "interpolation": "cubic"},
        )
        layer_result = call("list_layers")
        if not isinstance(layer_result, dict) or not layer_result.get("layers"):
            raise RuntimeError(f"unexpected list_layers response: {layer_result!r}")
        source_layer = layer_result["layers"][0]["name"]

        call("desaturate", {"mode": "luminosity", "layer_name": source_layer})
        call("convert_color_mode", {"mode": "GRAY"})
        _posterize(client)
        trace.append({"tool": "call_api", "operation": "gimp-drawable-posterize", "levels": 5})
        call("denoise", {"strength": DENOISE_STRENGTH, "layer_name": source_layer})
        _posterize(client)
        trace.append({"tool": "call_api", "operation": "gimp-drawable-posterize", "levels": 5})

        posterized_path = scene_dir / "posterized.png"
        call(
            "export_image",
            {"file_path": str(posterized_path), "format": "png", "flatten": True},
        )

        mask_layers: list[str] = []
        call("select_none")
        for _, value_name, value in LEVELS:
            layer_name = f"mask_{value_name}"
            call("create_layer", {"name": layer_name, "fill": "black"})
            call(
                "select_by_color",
                {
                    "color": f"#{value:02x}{value:02x}{value:02x}",
                    "threshold": 0,
                    "operation": "replace",
                    "layer_name": source_layer,
                },
            )
            call("fill_selection", {"color": "white", "layer_name": layer_name})
            call("select_none")
            call("set_layer_properties", {"layer_name": layer_name, "visible": False})
            mask_layers.append(layer_name)

        call("set_layer_properties", {"layer_name": source_layer, "visible": False})
        mask_paths: list[Path] = []
        for (filename, _, _), layer_name in zip(LEVELS, mask_layers, strict=True):
            path = layers_dir / filename
            call("set_layer_properties", {"layer_name": layer_name, "visible": True})
            call(
                "export_image",
                {"file_path": str(path), "format": "png", "flatten": True},
            )
            call("set_layer_properties", {"layer_name": layer_name, "visible": False})
            _normalize_exported_mask(path)
            mask_paths.append(path)
    finally:
        if opened:
            try:
                call("close_image", {"save_first": False})
            except Exception:
                pass

    cleanup = _clean_masks(mask_paths)

    with Image.open(source) as image:
        reference_image = image.convert("RGB")
    with Image.open(mask_paths[0]) as image:
        target_size = image.size
    if reference_image.size != target_size:
        reference_image = reference_image.resize(target_size, Image.Resampling.LANCZOS)
    reference = np.asarray(reference_image)

    masks: list[dict[str, Any]] = []
    for index, ((filename, name, value), path) in enumerate(
        zip(LEVELS, mask_paths, strict=True), start=1
    ):
        with Image.open(path) as image:
            foreground = np.asarray(image) == 255
        masks.append(
            {
                "index": index,
                "name": name,
                "path": f"layers/{filename}",
                "posterized_value": value,
                "target_rgb": _average_rgb(reference, foreground),
                "coverage": float(np.count_nonzero(foreground) / foreground.size),
            }
        )

    _contact_sheet(source, posterized_path, mask_paths, scene_dir / "contact-sheet.png")
    manifest = {
        "version": 1,
        "source": str(source),
        "source_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
        "width": target_size[0],
        "height": target_size[1],
        "method": {
            "engine": "GIMP 3.2.6 via maorcc/gimp-mcp under Xvfb",
            "value_space": "GIMP luminosity grayscale",
            "posterize_levels": POSTERIZE_LEVELS,
            "despeckle": (
                f"GEGL noise reduction strength {DENOISE_STRENGTH}, re-posterize, "
                f"then relabel components below {MIN_COMPONENT_FRACTION:.2%} of canvas "
                "to the adjacent value with the longest shared boundary"
            ),
        },
        "masks": masks,
        "mcp_trace": trace,
        "cleanup": cleanup,
    }
    (scene_dir / "manifest.json").write_text(
        json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
    )
    return manifest


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Decompose landscapes into five value masks through GIMP MCP."
    )
    parser.add_argument("inputs", nargs="+", type=Path)
    parser.add_argument("--output-dir", type=Path, default=Path("artifacts/spike-c"))
    parser.add_argument("--install-dir", type=Path, default=default_install_dir())
    parser.add_argument("--skip-install", action="store_true")
    args = parser.parse_args(argv)

    install_dir = args.install_dir.expanduser().resolve()
    if not args.skip_install:
        install_runtime(install_dir)

    output_dir = args.output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    scene_manifests: list[dict[str, Any]] = []
    with gimp_service(install_dir):
        with McpClient(install_dir) as client:
            for source in args.inputs:
                scene_name = source.stem
                scene_manifests.append(
                    decompose_scene(client, source, output_dir / scene_name)
                )

    reports = []
    for manifest in scene_manifests:
        scene_dir = output_dir / Path(manifest["source"]).stem
        report = validate_scene(scene_dir)
        manifest_path = scene_dir / "manifest.json"
        manifest_data = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest_data["validation"] = report.to_dict()
        manifest_path.write_text(
            json.dumps(manifest_data, indent=2) + "\n", encoding="utf-8"
        )
        reports.append({"scene": scene_dir.name, **report.to_dict()})

    paintable_scene_count = sum(report["paintable"] for report in reports)
    if paintable_scene_count == len(reports):
        paintability_result = "clean_regions"
    elif paintable_scene_count == 0:
        paintability_result = "confetti"
    else:
        paintability_result = "mixed"
    summary = {
        "version": 1,
        "scene_count": len(reports),
        "contract_pass": all(report["passed"] for report in reports),
        "paintable_scene_count": paintable_scene_count,
        "paintability_result": paintability_result,
        "scenes": reports,
    }
    (output_dir / "report.json").write_text(
        json.dumps(summary, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(summary, indent=2))
    return 0 if summary["contract_pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
