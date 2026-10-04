from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import numpy as np
from PIL import Image

from track2 import cv as cvmod
from track2 import mixfix
from track2.cv import DEFAULT_BARE_RGB, measure
from track2.machine import StepMachine
from track2.schema import Step, Verdict
from track2.simulate import SimFeed, run
from track2.watcher import Watcher

W, H = 120, 80
READY = Verdict(verdict="READY", category="none", adjustment="")


def make_ref() -> np.ndarray:
    ref = np.zeros((H, W, 3), np.uint8)
    ref[:H // 2] = (70, 120, 200)      # blue sky, top half
    ref[H // 2:] = (190, 110, 60)      # orange ground, bottom half
    return ref


def make_step(i: int, name: str) -> Step:
    return Step(index=i, name=name, mask_path=f"layers/{i}.png", target_rgb=(1, 2, 3),
                mix=[{"pigment": "titanium white", "parts": 1}], brush="1in flat",
                technique="t", stroke_dir_deg=0, success="ok")


def paint(ref: np.ndarray, mask: np.ndarray, rows_frac: float = 1.0, scale: float = 1.0,
          base: np.ndarray | None = None) -> np.ndarray:
    """Canvas with `mask` painted from the top down to rows_frac, scaled in brightness."""
    out = np.full_like(ref, DEFAULT_BARE_RGB) if base is None else base.copy()
    ys = np.mgrid[0:H, 0:W][0]
    region = (mask > 127) & (ys < H * rows_frac) if rows_frac < 1 else mask > 127
    out[region] = np.clip(ref[region].astype(np.float32) * scale, 0, 255).astype(np.uint8)
    return out


class MeasureTest(unittest.TestCase):
    def setUp(self) -> None:
        self.ref = make_ref()
        self.mask = np.zeros((H, W), np.uint8)
        self.mask[:H // 2] = 255

    def test_complete_correct(self) -> None:
        m = measure(paint(self.ref, self.mask), self.ref, self.mask)
        self.assertTrue(m.checkable)
        self.assertGreater(m.coverage, 0.99)
        self.assertLess(abs(m.delta_l), 1.0)

    def test_half_coverage(self) -> None:
        m = measure(paint(self.ref, self.mask, rows_frac=0.25), self.ref, self.mask)
        self.assertLess(m.coverage, 0.65)
        self.assertTrue(m.missing.any())

    def test_too_dark_is_negative_delta_l(self) -> None:
        m = measure(paint(self.ref, self.mask, scale=0.5), self.ref, self.mask)
        self.assertLess(m.delta_l, -12)

    def test_nothing_to_paint_is_not_checkable(self) -> None:
        blank_ref = np.full_like(self.ref, DEFAULT_BARE_RGB)
        self.assertFalse(measure(blank_ref, blank_ref, self.mask).checkable)

    def test_calibrates_bare_canvas_colour(self) -> None:
        self.assertEqual(cvmod.estimate_bare_rgb(np.full((4, 4, 3), (200, 190, 180), np.uint8)), (200, 190, 180))


class MachineReplaceTest(unittest.TestCase):
    def test_replace_remaining_keeps_finished_steps_and_resets_tries(self) -> None:
        m = StepMachine([make_step(1, "a"), make_step(2, "b"), make_step(3, "c")])
        m.submit(READY)
        for _ in range(3):
            m.submit(Verdict(verdict="ADJUST", category="value", adjustment="x"))
        self.assertEqual(m.status, "stuck")
        m.replace_remaining([make_step(2, "b2"), make_step(3, "c2")])
        self.assertEqual((m.status, m.state["tries"], [s.name for s in m.steps]), ("active", 0, ["a", "b2", "c2"]))
        self.assertEqual(m.current.name, "b2")


class MixFixTest(unittest.TestCase):
    def test_added_parts_reach_the_reference_lightness(self) -> None:
        t, lc, lr, lp = 10, 50.0, 60.0, 96.0
        p = mixfix.parts_to_add(t, lc, lr, lp)
        self.assertAlmostEqual((t * lc + p * lp) / (t + p), lr, places=6)

    def test_capped_and_unreachable_cases_never_exceed_the_mix(self) -> None:
        self.assertEqual(mixfix.parts_to_add(10, 20.0, 80.0, 96.0), 10.0)   # would need >10 parts: capped
        self.assertEqual(mixfix.parts_to_add(10, 50.0, 60.0, 12.0), 10.0)   # black cannot lighten

    def test_part_formatting(self) -> None:
        self.assertEqual([mixfix.fmt_parts(x) for x in (0.1, 1.0, 1.5, 2.0, 0.9)],
                         ["half a part", "1 part", "1.5 parts", "2 parts", "1 part"])

    def test_advice_picks_white_to_lighten_and_the_mixs_darkest_to_darken(self) -> None:
        step = Step(**{**make_step(1, "sky").model_dump(),
                       "mix": [{"pigment": "titanium white", "parts": 4},
                               {"pigment": "ultramarine blue", "parts": 2}]})
        self.assertIn("titanium white", mixfix.advise(step, 40.0, 60.0))
        self.assertIn("too dark", mixfix.advise(step, 40.0, 60.0))
        self.assertIn("ultramarine blue", mixfix.advise(step, 70.0, 50.0))
        self.assertIn("6-part mix", mixfix.advise(step, 70.0, 50.0))


class ValueMapTest(unittest.TestCase):
    def test_map_points_at_the_too_dark_half_only(self) -> None:
        ref = make_ref()
        mask = np.zeros((H, W), np.uint8)
        mask[:H // 2] = 255
        canvas = paint(ref, mask)
        canvas[:H // 2, :W // 2] = (canvas[:H // 2, :W // 2] * 0.4).astype(np.uint8)  # left half too dark
        m = measure(canvas, ref, mask, blur_px=9)
        off = m.value_off
        self.assertGreater(off[:H // 2, :W // 4].mean(), 0.9)       # left: flagged
        self.assertLess(off[:H // 2, 3 * W // 4:].mean(), 0.05)      # right: fine


class WatcherTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        (self.dir / "layers").mkdir()
        self.ref = make_ref()
        self.masks = []
        for i, sl in enumerate((slice(0, H // 2), slice(H // 2, H)), 1):
            m = np.zeros((H, W), np.uint8)
            m[sl] = 255
            Image.fromarray(m).save(self.dir / "layers" / f"{i}.png")
            self.masks.append(m)
        self.steps = [make_step(1, "sky"), make_step(2, "ground")]
        self.calls = 0
        self.feed = SimFeed((W, H))

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def watcher(self, critique_result: Verdict = READY) -> Watcher:
        def fake(canvas, step, mask):
            self.calls += 1
            return critique_result
        return Watcher(StepMachine(self.steps), self.ref, self.dir, self.feed.capture, fake)

    def test_hand_in_frame_never_triggers_a_check(self) -> None:
        w = self.watcher()
        events = run(w, self.feed, [(paint(self.ref, self.masks[0]), 20.0, 0.0)])
        self.assertEqual((events, self.calls), ([], 0))

    def test_complete_step_advances_after_settling_with_one_gemini_call(self) -> None:
        w = self.watcher()
        events = run(w, self.feed, [(paint(self.ref, self.masks[0]), 2.0, 10.0)])
        self.assertEqual([e.kind for _, e in events], ["advanced"])
        self.assertEqual(self.calls, 1)
        self.assertEqual(w.machine.current.index, 2)
        self.assertGreaterEqual(events[0][0], 2.0 + 1.5)  # not before the settle time

    def test_progress_midstroke_is_quiet_then_idle_gives_coverage_correction(self) -> None:
        w = self.watcher()
        part = paint(self.ref, self.masks[0], rows_frac=0.2)
        quiet = run(w, self.feed, [(part, 1.0, 4.0)])           # paused only 4s: still working
        self.assertEqual(quiet, [])
        events = run(w, self.feed, [(part, 0.0, 12.0)], t0=5.0)  # idle long enough
        self.assertEqual([(e.kind, e.verdict.category, e.source) for _, e in events],
                         [("correction", "coverage", "cv")])
        self.assertTrue(events[0][1].missing.any())
        self.assertEqual(self.calls, 0)                         # no API call for a CV correction

    def test_too_dark_gives_value_correction_without_gemini(self) -> None:
        w = self.watcher()
        events = run(w, self.feed, [(paint(self.ref, self.masks[0], scale=0.5), 1.0, 10.0)])
        self.assertEqual([(e.kind, e.verdict.category, e.source) for _, e in events],
                         [("correction", "value", "cv")])
        ev = events[0][1]
        self.assertIn("too dark", ev.verdict.adjustment)
        self.assertIn("part", ev.verdict.adjustment)          # a concrete mix fix, not just "lighten"
        self.assertTrue(ev.off_value.any() and ev.missing is None)  # a map of where, for the projector
        self.assertEqual(self.calls, 0)

    def test_correction_is_not_repeated_until_the_canvas_changes(self) -> None:
        w = self.watcher()
        dark = paint(self.ref, self.masks[0], scale=0.5)
        events = run(w, self.feed, [(dark, 1.0, 30.0)])
        self.assertEqual(len(events), 1)
        fixed = paint(self.ref, self.masks[0])
        events = run(w, self.feed, [(fixed, 2.0, 10.0)], t0=40.0)
        self.assertEqual([e.kind for _, e in events], ["advanced"])  # response to the change

    def test_gemini_adjust_is_emitted_immediately(self) -> None:
        w = self.watcher(Verdict(verdict="ADJUST", category="stroke_direction", adjustment="Paint horizontally."))
        events = run(w, self.feed, [(paint(self.ref, self.masks[0]), 1.0, 8.0)])
        self.assertEqual([(e.kind, e.source) for _, e in events], [("correction", "gemini")])

    def test_gemini_cannot_override_cv_on_coverage_or_value(self) -> None:
        w = self.watcher(Verdict(verdict="ADJUST", category="coverage", adjustment="Fill it in."))
        events = run(w, self.feed, [(paint(self.ref, self.masks[0]), 1.0, 8.0)])
        self.assertEqual([e.kind for _, e in events], ["advanced"])

    def test_resting_hand_blocks_the_check_until_it_leaves(self) -> None:
        w = self.watcher()
        done = paint(self.ref, self.masks[0])
        self.feed.resting_hand = True
        events = run(w, self.feed, [(done, 1.0, 20.0)])        # hand sits on the canvas, perfectly still
        self.assertEqual((events, self.calls), ([], 0))
        self.feed.resting_hand = False
        events = run(w, self.feed, [(done, 0.0, 6.0)], t0=30.0)  # hand leaves: capture now clean
        self.assertEqual([e.kind for _, e in events], ["advanced"])

    def test_change_outside_the_step_region_is_an_obstruction_for_a_while(self) -> None:
        w = self.watcher()
        step1 = paint(self.ref, self.masks[0])
        run(w, self.feed, [(paint(self.ref, self.masks[0], rows_frac=0.2), 1.0, 3.0)])  # a first accepted capture
        stray = step1.copy()
        stray[H // 2 + 5:H - 5, 10:W - 10] = (30, 30, 30)       # something dark lying on the later step's area
        events = run(w, self.feed, [(stray, 1.0, 8.0)], t0=10.0)
        self.assertEqual((events, self.calls), ([], 0))          # skipped, not judged

    def test_stuck_after_three_corrections(self) -> None:
        w = self.watcher()
        t = 0.0
        kinds = []
        for scale in (0.5, 0.45, 0.4):  # each repaint is still too dark: a new canvas, a new check
            ev = run(w, self.feed, [(paint(self.ref, self.masks[0], scale=scale), 1.0, 12.0)], t0=t)
            kinds += [e.kind for _, e in ev]
            t += 20.0
        self.assertEqual(kinds, ["correction", "correction", "stuck"])
        self.assertEqual(w.machine.status, "stuck")

    def _stuck_run(self, w: Watcher):
        kinds, t = [], 0.0
        for scale in (0.5, 0.45, 0.4):
            ev = run(w, self.feed, [(paint(self.ref, self.masks[0], scale=scale), 1.0, 12.0)], t0=t)
            kinds += [e for _, e in ev]
            t += 20.0
        return kinds

    def test_stuck_step_is_replanned_once_and_the_session_continues(self) -> None:
        revised = [make_step(1, "sky, simpler"), make_step(2, "ground")]
        asked = []

        def replan(position, history, canvas):
            asked.append((position, len(history), canvas.shape))
            return revised

        w = self.watcher()
        w._replan = replan
        events = self._stuck_run(w)
        self.assertEqual([e.kind for e in events], ["correction", "correction", "replanned"])
        self.assertEqual(events[-1].new_steps, revised)
        self.assertEqual((w.machine.status, w.machine.state["tries"], w.machine.current.name),
                         ("active", 0, "sky, simpler"))
        self.assertEqual(asked[0][0], 0)
        self.assertEqual(asked[0][2][:2], (H, W))                  # got the last real canvas, not a dummy

    def test_second_stuck_on_the_same_step_is_not_replanned_again(self) -> None:
        w = self.watcher()
        w._replan = lambda *a: [make_step(1, "sky, simpler"), make_step(2, "ground")]
        self._stuck_run(w)                                          # first stuck -> replanned
        kinds, t = [], 100.0
        for scale in (0.35, 0.3, 0.25):
            ev = run(w, self.feed, [(paint(self.ref, self.masks[0], scale=scale), 1.0, 12.0)], t0=t)
            kinds += [e.kind for _, e in ev]
            t += 20.0
        self.assertEqual(kinds, ["correction", "correction", "stuck"])
        self.assertEqual(w.machine.status, "stuck")

    def test_a_failing_replan_falls_back_to_stuck(self) -> None:
        def boom(*a):
            raise RuntimeError("api down")

        w = self.watcher()
        w._replan = boom
        events = self._stuck_run(w)
        self.assertEqual([e.kind for e in events], ["correction", "correction", "stuck"])
        self.assertEqual(w.machine.status, "stuck")

    def test_full_two_step_session_completes(self) -> None:
        w = self.watcher()
        step1 = paint(self.ref, self.masks[0])
        step2 = paint(self.ref, self.masks[1], base=step1)
        events = run(w, self.feed, [(step1, 2.0, 8.0), (step2, 2.0, 8.0)])
        self.assertEqual([e.kind for _, e in events], ["advanced", "complete"])


if __name__ == "__main__":
    unittest.main()
