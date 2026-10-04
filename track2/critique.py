"""critique(ref, capture, step) -> Verdict

Spike D: single samples can emit a spurious ADJUST on a correct painting, so we vote
over several parallel samples. Only value, coverage and stroke direction are asked for;
blending was not reliably detectable.
"""
from __future__ import annotations

import json
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from PIL import Image

from track2 import gemini
from track2.schema import Step, Verdict, verdict_is_consistent
from track2.voice import VOICE

PROMPT = """You are a painting coach checking a student's work on ONE step of a landscape.
Image 1 is the reference the student is copying. Image 2 is a photo of their canvas right
now (bare canvas is off-white).{mask_note} Judge ONLY the current step, nothing else. Earlier
steps are already painted; later steps' areas are intentionally still bare.

Current step:
{step}

If the step is done well enough to move on, return verdict READY, category none, empty
adjustment. Otherwise return verdict ADJUST with exactly ONE adjustment: the single most
important defect, as one gentle sentence that says what to do next. Never list several
problems. Do not invent problems on a step that is done. Categories: value = paint too
light/dark vs the target, coverage = parts of this step's region unpainted or incomplete,
stroke_direction = strokes not running the way the step says.

""" + VOICE


def vote(samples: list[Verdict]) -> Verdict:
    """READY unless a majority of samples say ADJUST; then the most common category wins."""
    if not samples:
        raise ValueError("no valid samples to vote over")
    adjusts = [s for s in samples if s.verdict == "ADJUST"]
    if len(adjusts) * 2 <= len(samples):
        return Verdict(verdict="READY", category="none", adjustment="")
    top = Counter(s.category for s in adjusts).most_common(1)[0][0]
    return next(s for s in adjusts if s.category == top)


def _load(img: Image.Image | Path | str) -> Image.Image:
    return img.convert("RGB") if isinstance(img, Image.Image) else Image.open(img).convert("RGB")


MASK_NOTE = (" Image 3 is this step's region: WHITE is where paint belongs now, black is not this"
             " step's business (ignore it, whether painted or bare). Judge coverage and value"
             " only inside the white area.")


CV_NOTE = (" Coverage and colour of this region have ALREADY been verified by measurement, so do"
           " not report coverage or value. Judge only whether the brush strokes run the way the"
           " step says (stroke_direction); if they do, return READY.")


def critique(ref, capture, step: Step, *, mask=None, cv_verified: bool = False, n: int = 3, client=None,
             model: str = gemini.DEFAULT_MODEL) -> Verdict:
    """`mask`: the step's mask image/path. Needed when regions are scattered (value bands).
    `cv_verified`: coverage/value were already measured locally; ask only about strokes."""
    ref_img, cap_img = _load(ref), _load(capture)
    images = [ref_img, cap_img] + ([_load(mask)] if mask is not None else [])
    client = client or gemini.make_client()
    # The mask path is a file pointer, not something Gemini can read; keep it out of the prompt.
    prompt = PROMPT.format(step=json.dumps(step.model_dump(exclude={"mask_path"}), indent=2),
                           mask_note=(MASK_NOTE if mask is not None else "") + (CV_NOTE if cv_verified else ""))

    def sample(_: int) -> Verdict | None:
        for _try in range(3):  # schema enforcement: re-ask if the reply is internally inconsistent
            v = gemini.generate(client, [prompt, *images], Verdict, model=model)
            if verdict_is_consistent(v):
                return v
        return None

    with ThreadPoolExecutor(max_workers=n) as pool:
        samples = [v for v in pool.map(sample, range(n)) if v is not None]
    return vote(samples)
