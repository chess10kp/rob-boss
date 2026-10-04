"""plan(ref, masks) -> [Step]   and   replan(...) -> [Step]

Gemini orders Track 3's masks into a teaching sequence and describes how to paint each one.
It never sees or returns coordinates: geometry stays in the mask files, and `target_rgb` is
measured from the reference, not guessed. `replan` revises the not-yet-finished steps when
the painter is stuck, with the same schema checks and the same masks.

Track 3 layer stacks (a scene_dir with track3.layers' report.json) are planned in Track 3's
order and only in that order: the layers overlap back to front, each painted over the ones
before it, so reordering would paint over finished work. Gemini still writes the mix (from
the kit in track2/palette.py), brush, technique and success check for every layer, and is
told each layer's role, opacity and how much of it later layers will cover.
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
from PIL import Image

from track2 import gemini
from track2.palette import BRUSHES, PIGMENTS
from track2.schema import MAX_TOTAL_PARTS, PlanDraft, Step, validate_draft
from track2.voice import VOICE

FIELDS = """For each region give:
- name: short, friendly title, e.g. "Let's put in a happy little sky"
- mix: pigments with integer parts that mix to roughly the region's mean color.
  Pigments must come from: {pigments}. Total parts at most {max_parts}.
- brush: one of {brushes}
- technique: one or two sentences on how to apply it (stroke style, blending, edges)
- stroke_dir_deg: dominant stroke direction, 0 = horizontal, 90 = vertical, 0..359
- success: one sentence a camera could check (coverage, value, stroke direction)

Never invent coordinates or shapes; refer to regions only by what they look like.

""" + VOICE

PROMPT = """You are an oil/acrylic painting teacher turning a reference image into a
step-by-step lesson for a beginner. The reference is image 1. It has been split into
{n} regions (masks); each mask follows, with its measured facts.

{facts}

{order}
""" + FIELDS + "\n{feedback}"

ORDER_FREE = ("Return ALL {n} regions in the best painting order for a beginner (steps list order is\n"
              "the teaching order; use each mask_id exactly once).")
ORDER_STACK = (
    "These regions are the layers of a back-to-front painting stack, listed in the order they\n"
    "must be painted: each layer goes on over the ones before it, and its paint may extend under\n"
    "parts that later layers will cover (painted_coverage_pct vs visible_coverage_pct; the mask\n"
    "shown is the part that stays visible). opacity < 1 means a thin, transparent pass; 1.0 means\n"
    "opaque body. Return ALL {n} in exactly this order, each mask_id once.")

REPLAN_PROMPT = """You are an oil/acrylic painting teacher. A beginner is STUCK on step {stuck}
of a lesson: they were corrected three times and the step still is not right. Image 1 is the
reference, image 2 is their canvas now. The unfinished regions follow, with measured facts.

{facts}

The lesson so far (finished steps are not shown again) planned these remaining steps:
{current}

The corrections they received on step {stuck}:
{history}

Rewrite the remaining steps so the student can succeed: {reorder}break the hard
move into a simpler technique, change the mix or brush, and make success criteria easier to
see. Word the revised step to reassure them: it is a happy accident, not a failure. Use each mask_id exactly once; keep step {stuck}'s region among them.
""" + FIELDS + "\n{feedback}"

REORDER_FREE = "you may reorder them, "
REORDER_STACK = "keep their order (it is fixed: later layers are painted over earlier ones), "


def track3_stack(scene_dir: Path | None) -> dict[str, dict] | None:
    """Per-mask facts from a track3.layers scene (report.json), in stack order; None otherwise."""
    report = Path(scene_dir) / "report.json" if scene_dir else None
    if report is None or not report.exists():
        return None
    return {Path(s["mask_path"]).stem: {
        "track3_name": s["name"], "role": s["role"], "opacity": s["opacity"],
        "painted_coverage_pct": round(s["painted_coverage"] * 100, 1),
        "visible_coverage_pct": round(s["visible_coverage"] * 100, 1),
        "stroke_axis_deg": int(round(s["stroke_dir_deg"])) % 180,
    } for s in json.loads(report.read_text())["steps"]}


def _stack_order(masks: list[Path], stack: dict[str, dict]) -> list[Path]:
    order = list(stack)
    missing = [m.stem for m in masks if m.stem not in stack]
    if missing:
        raise ValueError(f"masks not in the Track 3 stack: {missing}")
    return sorted(masks, key=lambda m: order.index(m.stem))


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


def _ask(client, model: str, build_prompt, images: list, ids: list[str], max_tries: int,
         keep_order: bool = False) -> PlanDraft:
    """One validated Gemini call: on schema violations, re-ask with the errors spelled out."""
    feedback = ""
    for _ in range(max_tries):
        draft = gemini.generate(client, [build_prompt(feedback), *images], PlanDraft, model=model)
        errors = validate_draft(draft, ids, keep_order=keep_order)
        if not errors:
            return draft
        feedback = "Your previous answer was invalid, fix these and answer again:\n- " + "\n- ".join(errors)
    raise ValueError(f"planner could not produce a valid plan after {max_tries} tries: {errors}")


def _mask_images(facts: list[dict], masks: list[Path]) -> list:
    out: list = []
    for f, m in zip(facts, masks):
        out += [f"mask_id={f['mask_id']}", _load(m)]
    return out


def _to_steps(draft: PlanDraft, facts: list[dict], masks: list[Path], start_index: int,
              scene_dir: Path | None) -> list[Step]:
    by_id = {f["mask_id"]: (f, m) for f, m in zip(facts, masks)}
    steps = []
    for i, d in enumerate(draft.steps, start_index):
        f, m = by_id[d.mask_id]
        rel = m.relative_to(scene_dir) if scene_dir else Path("layers") / m.name
        steps.append(Step(
            index=i, name=d.name, mask_path=rel.as_posix(), target_rgb=tuple(f["mean_rgb"]),
            mix=[{"pigment": x.pigment, "parts": x.parts} for x in d.mix], brush=d.brush,
            technique=d.technique, stroke_dir_deg=d.stroke_dir_deg, success=d.success,
        ))
    return steps


def plan(ref: Image.Image | Path | str, masks: list[Path], *, scene_dir: Path | None = None,
         client=None, model: str = gemini.DEFAULT_MODEL, max_tries: int = 3) -> list[Step]:
    """Order and describe `masks` (paths to 8-bit PNGs). `scene_dir` makes mask_path relative;
    if it holds a Track 3 layer stack, the stack's order is kept (see module docstring)."""
    ref_img = _load(ref)
    stack = track3_stack(scene_dir)
    masks = _stack_order(masks, stack) if stack else sorted(masks)
    facts = [describe_mask(ref_img, m) | (stack[m.stem] if stack else {}) for m in masks]
    client = client or gemini.make_client()
    facts_text = "\n".join(json.dumps(f) for f in facts)
    order = (ORDER_STACK if stack else ORDER_FREE).format(n=len(masks))

    def prompt(feedback: str) -> str:
        return PROMPT.format(n=len(masks), facts=facts_text, order=order, pigments=", ".join(PIGMENTS),
                             max_parts=MAX_TOTAL_PARTS, brushes=", ".join(BRUSHES), feedback=feedback)

    draft = _ask(client, model, prompt, [ref_img, *_mask_images(facts, masks)], [f["mask_id"] for f in facts],
                 max_tries, keep_order=bool(stack))
    return _to_steps(draft, facts, masks, 1, scene_dir)


def replan(ref: Image.Image | Path | str, capture: Image.Image | Path | str, steps: list[Step],
           stuck_position: int, history: list[dict], *, scene_dir: Path, client=None,
           model: str = gemini.DEFAULT_MODEL, max_tries: int = 3) -> list[Step]:
    """Revise steps[stuck_position:] (the stuck step and everything after it).

    Returns the new remaining steps, numbered to continue after the finished ones, covering the
    same masks exactly once. Steps before `stuck_position` are the caller's to keep.
    """
    ref_img, cap_img = _load(ref), _load(capture)
    remaining = steps[stuck_position:]
    masks = [Path(scene_dir) / s.mask_path for s in remaining]
    stack = track3_stack(scene_dir)
    facts = [describe_mask(ref_img, m) | (stack.get(m.stem, {}) if stack else {}) for m in masks]
    client = client or gemini.make_client()
    stuck = steps[stuck_position]
    current = json.dumps([s.model_dump(exclude={"mask_path"}) | {"mask_id": Path(s.mask_path).stem}
                          for s in remaining], indent=1)
    corrections = "\n".join(f"- ({h['category']}) {h['adjustment']}" for h in history
                            if h.get("step") == stuck.index and h.get("verdict") == "ADJUST") or "- (none recorded)"
    facts_text = "\n".join(json.dumps(f) for f in facts)

    def prompt(feedback: str) -> str:
        return REPLAN_PROMPT.format(stuck=stuck.index, facts=facts_text, current=current, history=corrections,
                                    reorder=REORDER_STACK if stack else REORDER_FREE,
                                    pigments=", ".join(PIGMENTS), max_parts=MAX_TOTAL_PARTS,
                                    brushes=", ".join(BRUSHES), feedback=feedback)

    draft = _ask(client, model, prompt, [ref_img, cap_img, *_mask_images(facts, masks)],
                 [f["mask_id"] for f in facts], max_tries, keep_order=bool(stack))
    return _to_steps(draft, facts, masks, stuck.index, Path(scene_dir))
