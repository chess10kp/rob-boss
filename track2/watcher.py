"""Hands-free progress watching: debounce on motion, measure locally, ask Gemini rarely.

No button. `Watcher.tick(frame, now)` is called a few times a second with a cheap frame
and decides what, if anything, to do:

  1. Motion gate (debounce): frame-to-frame change inside the current step's region (grown
     by `region_margin`) means the painter (hand/brush) is working there; do nothing. A
     check is only considered once that area has been still for `settle_s` - i.e. that long
     after the hand leaves the region - and only if it changed since the last check. A hand
     moving or resting elsewhere does not hold the check up.
  2. Local CV (cv.py): measure coverage and value inside the step's mask. Cheap and exact,
     so it runs on every settled change and never calls the API.
       - value off (once enough is painted)  -> value correction
       - coverage low and the painter has paused for `idle_coverage_s` -> coverage correction
       - coverage low but they are still working -> stay quiet (that is just progress)
     CV corrections need `confirm_gap_s` of agreement across two measurements.
  3. Looks complete -> the voted Gemini critique confirms. CV is authoritative for coverage
     and value, so Gemini may only object about stroke direction; any other objection is
     ignored. READY advances the step machine; a stroke_direction ADJUST becomes the correction.
  4. After a correction nothing more is said until the canvas changes (no flip-flop). Each
     correction is a strike, except a coverage nudge after the painter filled at least
     `progress_strike_pts` more of the region since the last one (that is progress, not a
     failure). After `max_tries` strikes the step is re-planned once (replan_fn); either way it
     stays active and keeps being watched and corrected - the painter can always skip it.
     `coverage` holds the latest measured coverage, for a live readout.
  5. Before measuring, a hand/brush guard (cv.occlusion) skips captures with skin-coloured
     pixels the reference lacks inside the grown region; it retries shortly. Optionally
     (`outside_change_blocks`) any change outside the region also counts as an obstruction.

Frames are BGR uint8 arrays in canvas space (rectified, like track1.Rig.capture_canvas), so
the step's mask applies to them. `peek` frames feed the motion gate and should be cheap (no
projector flash); `capture()` is the accurate flash-lit capture, called only when a check is due.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

import cv2
import numpy as np
from PIL import Image

from track2 import cv as cvmod
from track2 import mixfix
from track2.machine import StepMachine
from track2.schema import Step, Verdict


STACK_BARE = 245     # track3.layers' BARE_CANVAS: the picture before a stack's first step


@dataclass
class WatchConfig:
    settle_s: float = 2.0          # the step's region must be still this long before any check
    motion_t: float = 2.0          # mean abs gray diff (0-255) in the region above which the painter is "active"
    region_margin: float = 0.08    # region grown by this fraction of canvas width for motion and hands
    change_t: float = 1.5          # diff vs the last checked frame that counts as "changed"
    idle_coverage_s: float = 6.0   # pause needed before "unpainted area" is a mistake, not progress
    confirm_gap_s: float = 2.0     # CV corrections must hold across two measurements this far apart
    complete_cov: float = 0.90     # coverage at which the step looks done -> ask Gemini
    progress_strike_pts: float = 0.05  # a coverage nudge is no strike if coverage rose this much since the last
    min_cov_for_value: float = 0.25
    value_dl: float = 12.0         # |delta L*| beyond this is "too light/dark"
    paint_de: float = 12.0         # colour distance from bare canvas that counts as painted
    skin_frac_t: float = 0.02      # canvas fraction of unexpected skin-coloured pixels = hand in frame
    outside_change_t: float = 0.03  # canvas fraction changed outside the step region = something in the way
    recheck_gap_s: float = 1.0     # retry this soon after skipping an occluded capture
    occlusion_max_s: float = 12.0  # after this, stop treating outside-region change as an obstruction
    outside_change_blocks: bool = False  # treat change outside the region as an obstruction (off:
                                         # a hand resting elsewhere must not hold the check up)


@dataclass
class Event:
    kind: str                      # correction | advanced | complete | replanned
    step_index: int
    verdict: Verdict | None = None
    source: str = ""               # cv | gemini
    missing: np.ndarray | None = None  # mask-space bool map of still-bare areas (for projection)
    off_value: np.ndarray | None = None  # mask-space bool map of painted areas that are too light/dark
    now: float = 0.0
    new_steps: list[Step] | None = None  # replanned: the revised current + remaining steps


# (current step position, correction history, last unobstructed canvas RGB) -> revised remaining steps
ReplanFn = Callable[[int, list, np.ndarray], list[Step]]
CritiqueFn = Callable[[np.ndarray, Step, np.ndarray], Verdict]  # (canvas_rgb, step, mask) -> voted verdict


def _small_gray(bgr: np.ndarray) -> np.ndarray:
    return cv2.resize(cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY), (96, 64), interpolation=cv2.INTER_AREA).astype(np.float32)


def default_replan(ref_rgb: np.ndarray, machine: StepMachine, scene_dir: Path) -> ReplanFn:
    from track2.planner import replan

    def run(position: int, history: list, canvas_rgb: np.ndarray) -> list[Step]:
        return replan(Image.fromarray(ref_rgb), Image.fromarray(canvas_rgb), machine.steps, position,
                      history, scene_dir=scene_dir)

    return run


def default_critique(ref_rgb: np.ndarray):
    from track2.critique import critique

    def run(canvas_rgb: np.ndarray, step: Step, mask: np.ndarray) -> Verdict:
        return critique(Image.fromarray(ref_rgb), Image.fromarray(canvas_rgb), step,
                        mask=Image.fromarray(mask), cv_verified=True)

    return run


class Watcher:
    def __init__(self, machine: StepMachine, ref_rgb: np.ndarray, scene_dir: Path,
                 capture: Callable[[], np.ndarray], critique_fn: CritiqueFn | None = None,
                 config: WatchConfig | None = None, bare_rgb=cvmod.DEFAULT_BARE_RGB,
                 replan_fn: ReplanFn | None = None, log: Callable[[str], None] | None = None):
        self.machine, self.ref, self.scene_dir = machine, ref_rgb, Path(scene_dir)
        self.capture, self.cfg, self.bare_rgb = capture, config or WatchConfig(), bare_rgb
        self.bare_image: np.ndarray | None = None
        self._critique = critique_fn or default_critique(ref_rgb)
        self._replan = replan_fn
        self._log = log or (lambda msg: None)
        self._replanned: set[int] = set()   # step indexes already re-planned once: never loop
        self._masks: dict[int, tuple] = {}
        self._frames: dict[str, tuple[Path | None, Path]] | None = None
        self._grown: dict[int, np.ndarray] = {}
        self._reset_step()
        self._prev: np.ndarray | None = None

    def calibrate(self, empty_canvas_bgr: np.ndarray) -> None:
        """Learn the bare canvas from a capture of it empty: its colour, and per pixel how it looks."""
        rgb = cv2.cvtColor(empty_canvas_bgr, cv2.COLOR_BGR2RGB)
        self.bare_rgb = cvmod.estimate_bare_rgb(rgb)
        self.bare_image = rgb            # per-pixel: the capture light is not even across the canvas

    def _reset_step(self) -> None:
        self._still_since: float | None = None
        self._checked: np.ndarray | None = None   # peek frame at the last check
        self._recheck_at: float | None = None
        self._streak: tuple[str, float] | None = None  # (category, time first seen)
        self._last_canvas: np.ndarray | None = None    # last capture accepted as unobstructed
        self._occluded_since: float | None = None
        self._nudged_cov = 0.0                         # coverage at the last coverage nudge (step starts bare)
        self.coverage: float | None = None             # latest measured coverage of this step, 0..1

    def rebase(self, canvas_bgr: np.ndarray) -> None:
        """A step is finished: the canvas as it is now is the starting point for the next one."""
        self.bare_image = cv2.cvtColor(canvas_bgr, cv2.COLOR_BGR2RGB)

    def _stack_frames(self) -> dict[str, tuple[Path | None, Path]]:
        """Track 3 layer stack: mask stem -> (expected picture before the step, after it)."""
        if self._frames is None:
            self._frames, before = {}, None
            report = self.scene_dir / "report.json"
            for s in json.loads(report.read_text())["steps"] if report.exists() else []:
                if s.get("step_path"):
                    after = self.scene_dir / s["step_path"]
                    self._frames[Path(s["mask_path"]).stem] = (before, after)
                    before = after
        return self._frames

    def _target(self, step: Step) -> tuple[np.ndarray, np.ndarray | None, np.ndarray]:
        """(reference, expected-before, mask) to measure the step against.

        Layer stack: the step's frame is the reference and the mask is where it differs from
        the previous frame (what this step changes, under later layers too, so it can be
        checked as it is painted). Otherwise: the final reference and the step's own mask."""
        if step.index not in self._masks:
            frames = self._stack_frames().get(Path(step.mask_path).stem)
            if frames:
                after = np.asarray(Image.open(frames[1]).convert("RGB"))
                before = np.asarray(Image.open(frames[0]).convert("RGB")) if frames[0] else \
                    np.full_like(after, STACK_BARE)
                change = np.linalg.norm(cvmod.to_lab(after) - cvmod.to_lab(before), axis=2) > self.cfg.paint_de
                self._masks[step.index] = (after, before, np.where(change, 255, 0).astype(np.uint8))
            else:
                mask = np.asarray(Image.open(self.scene_dir / step.mask_path).convert("L"))
                self._masks[step.index] = (self.ref, None, mask)
        return self._masks[step.index]

    def _mask(self, step: Step) -> np.ndarray:
        return self._target(step)[2]

    def _grown_region(self, step: Step) -> np.ndarray:
        """The step's region grown by region_margin (bool, mask size): where hands and motion count."""
        if step.index not in self._grown:
            mask = self._mask(step) > 127
            r = max(1, int(round(self.cfg.region_margin * mask.shape[1])))
            k = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * r + 1, 2 * r + 1))
            self._grown[step.index] = cv2.dilate(mask.astype(np.uint8), k).astype(bool)
        return self._grown[step.index]

    def _region_diff(self, small: np.ndarray, other: np.ndarray, step: Step) -> float:
        """Mean change between two small frames inside the grown region (whole frame if it is empty)."""
        region = cv2.resize(self._grown_region(step).astype(np.uint8), small.shape[::-1],
                            interpolation=cv2.INTER_NEAREST).astype(bool)
        diff = np.abs(small - other)
        return float(diff[region].mean()) if region.any() else float(diff.mean())

    def tick(self, peek_bgr: np.ndarray, now: float) -> Event | None:
        cfg, step = self.cfg, self.machine.current
        small = _small_gray(peek_bgr)
        prev, self._prev = self._prev, small
        if step is None or self.machine.status != "active" or prev is None:
            return None

        if self._region_diff(small, prev, step) > cfg.motion_t:         # painter active in the region -> debounce
            self._still_since = None
            return None
        if self._still_since is None:
            self._still_since = now
        if now - self._still_since < cfg.settle_s:
            return None

        changed = self._checked is None or self._region_diff(small, self._checked, step) > cfg.change_t
        if not changed and (self._recheck_at is None or now < self._recheck_at):
            return None                                             # nothing new to look at
        self._checked, self._recheck_at = small, None

        target, before, mask = self._target(step)
        canvas = cv2.cvtColor(self.capture(), cv2.COLOR_BGR2RGB)
        if self._obstructed(canvas, mask, step, now):               # hand/brush in the way: look again soon
            self._recheck_at = now + cfg.recheck_gap_s
            return None
        m = cvmod.measure(canvas, target, mask, bare_rgb=self.bare_rgb, bare_image=self.bare_image,
                          before_rgb=before, paint_de=cfg.paint_de, value_dl=cfg.value_dl)
        self.coverage = m.coverage if m.checkable else None

        verdict, source = None, "cv"
        if m.checkable and m.delta_l is not None and m.coverage >= cfg.min_cov_for_value \
                and abs(m.delta_l) > cfg.value_dl:
            verdict = self._value_verdict(step, m)
        elif m.checkable and m.coverage < cfg.complete_cov:
            if now - self._still_since >= cfg.idle_coverage_s:
                verdict = Verdict(verdict="ADJUST", category="coverage",
                                  adjustment=f"There's still a little bare canvas waiting for some love - about "
                                             f"{(1 - m.coverage) * 100:.0f}% of this area. Let's go "
                                             "back in and fill it right in.")
            else:                                                   # still working: progress, not a mistake
                self._recheck_at = self._still_since + cfg.idle_coverage_s
                self._streak = None
                return None
        else:                                                       # looks done -> Gemini confirms
            verdict, source = self._critique(canvas, step, mask), "gemini"
            # CV already verified coverage and value; Gemini may only object about strokes.
            if verdict.verdict == "READY" or verdict.category != "stroke_direction":
                verdict = Verdict(verdict="READY", category="none", adjustment="")
                self.rebase(cv2.cvtColor(canvas, cv2.COLOR_RGB2BGR))
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
        strike = True
        if verdict.category == "coverage":         # filling in steadily is progress, not a failed try
            strike = m.coverage - self._nudged_cov < cfg.progress_strike_pts
            self._nudged_cov = m.coverage
        return self._correct(step, verdict, source, now, strike,
                             missing=m.missing if verdict.category == "coverage" else None,
                             off_value=m.value_off if verdict.category == "value" else None)

    def _obstructed(self, canvas: np.ndarray, mask: np.ndarray, step: Step, now: float) -> bool:
        cfg = self.cfg
        target, _, _ = self._target(step)
        expected = [img for img in (self.ref, self.bare_image) if img is not None and img is not target]
        occ = cvmod.occlusion(canvas, target, mask, self._last_canvas if cfg.outside_change_blocks else None,
                              within=self._grown_region(step), also_expected=expected)
        if occ.skin_frac > cfg.skin_frac_t:
            if self._occluded_since is None:
                self._occluded_since = now
                self._log(f"check skipped: {occ.skin_frac:.1%} of the canvas looks like a hand near step "
                          f"{step.index}; retrying every {cfg.recheck_gap_s:g} s")
            return True
        if cfg.outside_change_blocks and occ.outside_change_frac > cfg.outside_change_t:
            if self._occluded_since is None:
                self._occluded_since = now
                self._log(f"check skipped: {occ.outside_change_frac:.1%} of the canvas changed outside step "
                          f"{step.index}; retrying for up to {cfg.occlusion_max_s:g} s")
            if now - self._occluded_since < cfg.occlusion_max_s:
                return True
        self._occluded_since = None                                  # clear (or accept a lasting change)
        self._last_canvas = canvas
        return False

    @staticmethod
    def _value_verdict(step: Step, m: cvmod.Measurement) -> Verdict:
        return Verdict(verdict="ADJUST", category="value",
                       adjustment=mixfix.advise(step, m.mean_l_canvas, m.mean_l_ref))

    def _correct(self, step, verdict, source, now, strike=True, missing=None, off_value=None) -> Event:
        if self.machine.submit(verdict, strike=strike) == "struggling":
            revised = self._try_replan(step)
            if revised:
                self._reset_step()
                self._checked = self._prev   # give the painter time to act: no re-check until the canvas changes
                return Event("replanned", step.index, verdict, source, now=now, new_steps=revised)
        return Event("correction", step.index, verdict, source, missing, off_value, now)

    def _try_replan(self, step: Step) -> list[Step] | None:
        """On a struggling step, ask for a revised plan once; any failure leaves the plan as it is."""
        if self._replan is None or step.index in self._replanned:
            return None
        self._replanned.add(step.index)
        position = self.machine.state["current"]
        canvas = self._last_canvas if self._last_canvas is not None else np.zeros((8, 8, 3), np.uint8)
        try:
            revised = self._replan(position, self.machine.state["history"], canvas)
        except Exception as e:
            self._log(f"re-plan of step {step.index} failed ({type(e).__name__}: {e}); keeping the plan")
            return None
        self.machine.replace_remaining(revised)
        return revised

    def _advance(self, step, verdict, now) -> Event:
        status = self.machine.submit(verdict)
        self._reset_step()
        return Event("complete" if status == "complete" else "advanced", step.index, verdict, "gemini", now=now)
