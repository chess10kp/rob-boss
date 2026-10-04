"""plan(ref, masks) -> [Step]

Gemini orders Track 3's masks into a teaching sequence and describes how to paint
each one. It never sees or returns coordinates: geometry stays in the mask files, and
`target_rgb` is measured from the reference, not guessed.
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
from PIL import Image

from track2 import gemini
from track2.palette import BRUSHES, PIGMENTS
from track2.schema import MAX_TOTAL_PARTS, PlanDraft, Step, validate_draft

PROMPT = """You are an oil/acrylic painting teacher turning a reference image into a
step-by-step lesson for a beginner. The reference is image 1. It has been split into
{n} regions (masks); each mask follows, with its measured facts.

{facts}

Return ALL {n} regions in the best painting order for a beginner (steps list order is
the teaching order; use each mask_id exactly once). For each, give:
- name: short imperative title, e.g. "Block in the sky"
- mix: pigments with integer parts that mix to roughly the region's mean color.
  Pigments must come from: {pigments}. Total parts at most {max_parts}.
- brush: one of {brushes}
- technique: one sentence on how to apply it (stroke style, blending, edges)
- stroke_dir_deg: dominant stroke direction, 0 = horizontal, 90 = vertical, 0..359
- success: one sentence a camera could check (coverage, value, stroke direction)

Never invent coordinates or shapes; refer to regions only by what they look like.
{feedback}"""


def _load(img: Image.Image | Path | str) -> Image.Image:
    return img.convert("RGB") if isinstance(img, Image.Image) else Image.open(img).convert("RGB")


def describe_mask(ref: Image.Image, mask_path: Path) -> dict:
    mask = np.asarray(Image.open(mask_path).convert("L")) == 255
    rgb = np.asarray(ref.resize(mask.shape[::-1], Image.LANCZOS))
    ys = np.nonzero(mask.any(axis=1))[0]
    return {
        "mask_id": mask_path.stem,
        "coverage_pct": round(float(mask.mean()) * 100, 1),
        "mean_rgb": [int(v) for v in rgb[mask].mean(axis=0)] if mask.any() else [0, 0, 0],
        "vertical_extent_pct": [int(ys.min() * 100 / mask.shape[0]), int(ys.max() * 100 / mask.shape[0])]
        if ys.size else [0, 0],
    }


def plan(ref: Image.Image | Path | str, masks: list[Path], *, scene_dir: Path | None = None,
         client=None, model: str = gemini.DEFAULT_MODEL, max_tries: int = 3) -> list[Step]:
    """Order and describe `masks` (paths to 8-bit PNGs). `scene_dir` makes mask_path relative."""
    ref_img = _load(ref)
    masks = sorted(masks)
    facts = [describe_mask(ref_img, m) for m in masks]
    ids = [f["mask_id"] for f in facts]
    client = client or gemini.make_client()

    contents_tail: list = []
    for f, m in zip(facts, masks):
        contents_tail += [f"mask_id={f['mask_id']}", _load(m)]
    facts_text = "\n".join(json.dumps(f) for f in facts)

    feedback = ""
    draft: PlanDraft | None = None
    for _ in range(max_tries):
        prompt = PROMPT.format(n=len(masks), facts=facts_text, pigments=", ".join(PIGMENTS),
                               max_parts=MAX_TOTAL_PARTS, brushes=", ".join(BRUSHES), feedback=feedback)
        draft = gemini.generate(client, [prompt, ref_img, *contents_tail], PlanDraft, model=model)
        errors = validate_draft(draft, ids)
        if not errors:
            break
        feedback = "Your previous answer was invalid, fix these and answer again:\n- " + "\n- ".join(errors)
    else:
        raise ValueError(f"planner could not produce a valid plan after {max_tries} tries: {errors}")

    by_id = {f["mask_id"]: (f, m) for f, m in zip(facts, masks)}
    steps = []
    for i, d in enumerate(draft.steps, 1):
        f, m = by_id[d.mask_id]
        rel = m.relative_to(scene_dir) if scene_dir else Path("layers") / m.name
        steps.append(Step(
            index=i, name=d.name, mask_path=rel.as_posix(), target_rgb=tuple(f["mean_rgb"]),
            mix=[{"pigment": x.pigment, "parts": x.parts} for x in d.mix], brush=d.brush,
            technique=d.technique, stroke_dir_deg=d.stroke_dir_deg, success=d.success,
        ))
    return steps
