"""Hands-free progress watching: debounce on motion, measure locally, ask Gemini rarely.

No button. `Watcher.tick(frame, now)` is called a few times a second with a cheap frame
and decides what, if anything, to do:

  1. Motion gate (debounce): any frame-to-frame change means the painter (hand/brush) is
     active; do nothing. A check is only considered after the canvas has been still for
     `settle_s`, and only if it changed since the last check.
  2. Local CV (cv.py): measure coverage and value inside the step's mask. Cheap and exact,
     so it runs on every settled change and never calls the API.
       - value off (once enough is painted)  -> value correction
       - coverage low and the painter has paused for `idle_coverage_s` -> coverage correction
       - coverage low but they are still working -> stay quiet (that is just progress)
     CV corrections need `confirm_gap_s` of agreement across two measurements.
  3. Looks complete -> the voted Gemini critique confirms. CV is authoritative for coverage
     and value, so Gemini may only object about stroke direction; any other objection is
     ignored. READY advances the step machine; a stroke_direction ADJUST becomes the correction.
  4. After a correction nothing more is said until the canvas changes (no flip-flop).
  5. Before measuring, a hand/brush guard (cv.occlusion) skips captures that contain skin-coloured
     pixels the reference lacks, or change outside the step's region; it retries shortly.

Frames are BGR uint8 arrays, matching track1.Rig. `peek` frames feed the motion gate and
should be cheap (no projector flash); `capture()` is the accurate flash-lit canvas capture,
called only when a check is actually due.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

import cv2
import numpy as np
from PIL import Image

from track2 import cv as cvmod
from track2.machine import StepMachine
from track2.schema import Step, Verdict


@dataclass
class WatchConfig:
    settle_s: float = 1.5          # canvas must be still this long before any check
    motion_t: float = 2.0          # mean abs gray diff (0-255) above which the painter is "active"
    change_t: float = 1.5          # diff vs the last checked frame that counts as "changed"
    idle_coverage_s: float = 6.0   # pause needed before "unpainted area" is a mistake, not progress
    confirm_gap_s: float = 2.0     # CV corrections must hold across two measurements this far apart
    complete_cov: float = 0.90     # coverage at which the step looks done -> ask Gemini
    min_cov_for_value: float = 0.25
    value_dl: float = 12.0         # |delta L*| beyond this is "too light/dark"
    paint_de: float = 12.0         # colour distance from bare canvas that counts as painted
    skin_frac_t: float = 0.02      # canvas fraction of unexpected skin-coloured pixels = hand in frame
    outside_change_t: float = 0.03  # canvas fraction changed outside the step region = something in the way
    recheck_gap_s: float = 1.0     # retry this soon after skipping an occluded capture
    occlusion_max_s: float = 12.0  # after this, stop treating outside-region change as an obstruction


@dataclass
class Event:
    kind: str                      # correction | advanced | complete | stuck
    step_index: int
    verdict: Verdict | None = None
    source: str = ""               # cv | gemini
    missing: np.ndarray | None = None  # canvas-space bool map of still-bare areas (for projection)
    now: float = 0.0


CritiqueFn = Callable[[np.ndarray, Step, np.ndarray], Verdict]  # (canvas_rgb, step, mask) -> voted verdict


def _small_gray(bgr: np.ndarray) -> np.ndarray:
    return cv2.resize(cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY), (96, 64), interpolation=cv2.INTER_AREA).astype(np.float32)


def default_critique(ref_rgb: np.ndarray):
    from track2.critique import critique

    def run(canvas_rgb: np.ndarray, step: Step, mask: np.ndarray) -> Verdict:
        return critique(Image.fromarray(ref_rgb), Image.fromarray(canvas_rgb), step,
                        mask=Image.fromarray(mask), cv_verified=True)

    return run


class Watcher:
    def __init__(self, machine: StepMachine, ref_rgb: np.ndarray, scene_dir: Path,
                 capture: Callable[[], np.ndarray], critique_fn: CritiqueFn | None = None,
                 config: WatchConfig | None = None, bare_rgb=cvmod.DEFAULT_BARE_RGB):
        self.machine, self.ref, self.scene_dir = machine, ref_rgb, Path(scene_dir)
        self.capture, self.cfg, self.bare_rgb = capture, config or WatchConfig(), bare_rgb
        self._critique = critique_fn or default_critique(ref_rgb)
        self._masks: dict[int, np.ndarray] = {}
        self._reset_step()
        self._prev: np.ndarray | None = None

    def calibrate(self, empty_canvas_bgr: np.ndarray) -> None:
        """Learn the bare-canvas colour from a capture of the empty canvas."""
        self.bare_rgb = cvmod.estimate_bare_rgb(cv2.cvtColor(empty_canvas_bgr, cv2.COLOR_BGR2RGB))

    def _reset_step(self) -> None:
        self._still_since: float | None = None
        self._checked: np.ndarray | None = None   # peek frame at the last check
        self._recheck_at: float | None = None
        self._streak: tuple[str, float] | None = None  # (category, time first seen)
        self._last_canvas: np.ndarray | None = None    # last capture accepted as unobstructed
        self._occluded_since: float | None = None

    def _mask(self, step: Step) -> np.ndarray:
        if step.index not in self._masks:
            self._masks[step.index] = np.asarray(Image.open(self.scene_dir / step.mask_path).convert("L"))
        return self._masks[step.index]

    def tick(self, peek_bgr: np.ndarray, now: float) -> Event | None:
        cfg, step = self.cfg, self.machine.current
        small = _small_gray(peek_bgr)
        prev, self._prev = self._prev, small
        if step is None or self.machine.status != "active" or prev is None:
            return None

        if float(np.abs(small - prev).mean()) > cfg.motion_t:      # painter active -> debounce
            self._still_since = None
            return None
        if self._still_since is None:
            self._still_since = now
        if now - self._still_since < cfg.settle_s:
            return None

        changed = self._checked is None or float(np.abs(small - self._checked).mean()) > cfg.change_t
        if not changed and (self._recheck_at is None or now < self._recheck_at):
            return None                                             # nothing new to look at
        self._checked, self._recheck_at = small, None

        mask = self._mask(step)
        canvas = cv2.cvtColor(self.capture(), cv2.COLOR_BGR2RGB)
        if self._obstructed(canvas, mask, now):                     # hand/brush in the way: look again soon
            self._recheck_at = now + cfg.recheck_gap_s
            return None
        m = cvmod.measure(canvas, self.ref, mask, bare_rgb=self.bare_rgb, paint_de=cfg.paint_de)

        verdict, source = None, "cv"
        if m.checkable and m.delta_l is not None and m.coverage >= cfg.min_cov_for_value \
                and abs(m.delta_l) > cfg.value_dl:
            verdict = self._value_verdict(m.delta_l)
        elif m.checkable and m.coverage < cfg.complete_cov:
            if now - self._still_since >= cfg.idle_coverage_s:
                verdict = Verdict(verdict="ADJUST", category="coverage",
                                  adjustment=f"Fill in the unpainted areas ({(1 - m.coverage) * 100:.0f}% of this "
                                             "step's region is still bare).")
            else:                                                   # still working: progress, not a mistake
                self._recheck_at = self._still_since + cfg.idle_coverage_s
                self._streak = None
                return None
        else:                                                       # looks done -> Gemini confirms
            verdict, source = self._critique(canvas, step, mask), "gemini"
            # CV already verified coverage and value; Gemini may only object about strokes.
            if verdict.verdict == "READY" or verdict.category != "stroke_direction":
                verdict = Verdict(verdict="READY", category="none", adjustment="")
                return self._advance(step, verdict, now)

        if source == "cv":                                          # debounce CV corrections over time
            if self._streak is None or self._streak[0] != verdict.category:
                self._streak = (verdict.category, now)
                self._recheck_at = now + cfg.confirm_gap_s
                return None
            if now - self._streak[1] < cfg.confirm_gap_s:
                self._recheck_at = self._streak[1] + cfg.confirm_gap_s
                return None
        self._streak = None
        return self._correct(step, verdict, source, m.missing if verdict.category == "coverage" else None, now)

    def _obstructed(self, canvas: np.ndarray, mask: np.ndarray, now: float) -> bool:
        cfg = self.cfg
        occ = cvmod.occlusion(canvas, self.ref, mask, self._last_canvas)
        if occ.skin_frac > cfg.skin_frac_t:
            self._occluded_since = self._occluded_since if self._occluded_since is not None else now
            return True
        if occ.outside_change_frac > cfg.outside_change_t:
            if self._occluded_since is None:
                self._occluded_since = now
            if now - self._occluded_since < cfg.occlusion_max_s:
                return True
        self._occluded_since = None                                  # clear (or accept a lasting change)
        self._last_canvas = canvas
        return False

    @staticmethod
    def _value_verdict(delta_l: float) -> Verdict:
        if delta_l < 0:
            text = "The paint is too dark: mix in more titanium white and lighten it."
        else:
            text = "The paint is too light: add a little darker pigment to the mix."
        return Verdict(verdict="ADJUST", category="value", adjustment=text)

    def _correct(self, step, verdict, source, missing, now) -> Event:
        status = self.machine.submit(verdict)
        kind = "stuck" if status == "stuck" else "correction"
        return Event(kind, step.index, verdict, source, missing, now)

    def _advance(self, step, verdict, now) -> Event:
        status = self.machine.submit(verdict)
        self._reset_step()
        return Event("complete" if status == "complete" else "advanced", step.index, verdict, "gemini", None, now)
