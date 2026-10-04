"""Track 2 data contracts.

`Step` is the object that crosses track boundaries (see PLAN.md, step.json).
The *Draft / *Reply models are what Gemini is asked to produce; they stay free of
numeric constraints because Gemini's schema subset rejects them. Real validation
lives in `validate_draft` and `Step`.
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from track2.palette import BRUSHES, PIGMENTS

MAX_TOTAL_PARTS = 12


class MixPart(BaseModel):
    pigment: str
    parts: int = Field(ge=1, le=MAX_TOTAL_PARTS)


class Step(BaseModel):
    index: int = Field(ge=1)
    name: str
    mask_path: str
    target_rgb: tuple[int, int, int]
    mix: list[MixPart] = Field(min_length=1)
    brush: str
    technique: str
    stroke_dir_deg: int = Field(ge=0, lt=360)
    success: str


# ---- what the planner asks Gemini for --------------------------------------------
class MixDraft(BaseModel):
    pigment: str
    parts: int


class StepDraft(BaseModel):
    mask_id: str
    name: str
    mix: list[MixDraft]
    brush: str
    technique: str
    stroke_dir_deg: int
    success: str


class PlanDraft(BaseModel):
    steps: list[StepDraft]


def validate_draft(draft: PlanDraft, mask_ids: list[str]) -> list[str]:
    """Return a list of violations (empty = valid). Fed back to Gemini on retry."""
    errors: list[str] = []
    got = [s.mask_id for s in draft.steps]
    if sorted(got) != sorted(mask_ids):
        errors.append(f"steps must use each of {mask_ids} exactly once, got {got}")
    for s in draft.steps:
        tag = f"{s.mask_id}"
        if not s.mix:
            errors.append(f"{tag}: mix is empty")
        total = 0
        for m in s.mix:
            if m.pigment not in PIGMENTS:
                errors.append(f"{tag}: pigment {m.pigment!r} not in palette {list(PIGMENTS)}")
            if not 1 <= m.parts <= MAX_TOTAL_PARTS:
                errors.append(f"{tag}: parts must be 1..{MAX_TOTAL_PARTS}, got {m.parts}")
            total += m.parts
        if total > MAX_TOTAL_PARTS:
            errors.append(f"{tag}: total parts {total} exceeds {MAX_TOTAL_PARTS}")
        if s.brush not in BRUSHES:
            errors.append(f"{tag}: brush {s.brush!r} not in {list(BRUSHES)}")
        if not 0 <= s.stroke_dir_deg < 360:
            errors.append(f"{tag}: stroke_dir_deg must be 0..359, got {s.stroke_dir_deg}")
        for field in ("name", "technique", "success"):
            if not getattr(s, field).strip():
                errors.append(f"{tag}: {field} is empty")
    return errors


# ---- what the critique asks Gemini for -------------------------------------------
# "blending" is deliberately absent: Spike D showed it is not reliably detectable.
Category = Literal["value", "coverage", "stroke_direction", "none"]


class Verdict(BaseModel):
    verdict: Literal["READY", "ADJUST"]
    category: Category
    adjustment: str


def verdict_is_consistent(v: Verdict) -> bool:
    if v.verdict == "READY":
        return v.category == "none" and not v.adjustment.strip()
    return v.category != "none" and bool(v.adjustment.strip())
