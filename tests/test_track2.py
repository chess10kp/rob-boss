from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

import numpy as np
from PIL import Image

from track2 import critique as critique_mod
from track2 import planner
from track2.machine import StepMachine
from track2.mock_layers import make_value_masks
from track2.schema import (MixDraft, PlanDraft, Step, StepDraft, Verdict,
                           validate_draft, verdict_is_consistent)
from track3.validate import validate_scene

READY = Verdict(verdict="READY", category="none", adjustment="")


def adjust(cat: str, text: str = "Do the thing.") -> Verdict:
    return Verdict(verdict="ADJUST", category=cat, adjustment=text)


def draft_step(mask_id: str, pigment: str = "titanium white", parts: int = 2) -> StepDraft:
    return StepDraft(mask_id=mask_id, name="Block in", mix=[MixDraft(pigment=pigment, parts=parts)],
                     brush="1in flat", technique="flat wash", stroke_dir_deg=0, success="covered")


def make_step(i: int = 1) -> Step:
    return Step(index=i, name="s", mask_path="layers/x.png", target_rgb=(1, 2, 3),
                mix=[{"pigment": "titanium white", "parts": 1}], brush="1in flat",
                technique="t", stroke_dir_deg=0, success="ok")


class ValidateDraftTest(unittest.TestCase):
    def test_valid(self) -> None:
        self.assertEqual(validate_draft(PlanDraft(steps=[draft_step("a"), draft_step("b")]), ["a", "b"]), [])

    def test_rejects_missing_and_duplicate_masks(self) -> None:
        errs = validate_draft(PlanDraft(steps=[draft_step("a"), draft_step("a")]), ["a", "b"])
        self.assertTrue(any("exactly once" in e for e in errs))

    def test_rejects_unknown_pigment_and_too_many_parts(self) -> None:
        errs = validate_draft(PlanDraft(steps=[draft_step("a", "unobtainium", 20)]), ["a"])
        self.assertTrue(any("not in palette" in e for e in errs))
        self.assertTrue(any("exceeds" in e or "parts must be" in e for e in errs))


class VerdictTest(unittest.TestCase):
    def test_consistency(self) -> None:
        self.assertTrue(verdict_is_consistent(READY))
        self.assertTrue(verdict_is_consistent(adjust("value")))
        self.assertFalse(verdict_is_consistent(Verdict(verdict="ADJUST", category="none", adjustment="x")))
        self.assertFalse(verdict_is_consistent(Verdict(verdict="READY", category="value", adjustment="")))
        self.assertFalse(verdict_is_consistent(Verdict(verdict="ADJUST", category="value", adjustment=" ")))

    def test_vote_majority_ready_beats_one_spurious_adjust(self) -> None:
        self.assertEqual(critique_mod.vote([READY, READY, adjust("coverage")]).verdict, "READY")

    def test_vote_majority_adjust_picks_top_category(self) -> None:
        v = critique_mod.vote([adjust("value", "a"), adjust("coverage", "b"), adjust("coverage", "c")])
        self.assertEqual((v.verdict, v.category), ("ADJUST", "coverage"))

    def test_vote_two_samples_split_is_ready(self) -> None:
        self.assertEqual(critique_mod.vote([READY, adjust("value")]).verdict, "READY")

    def test_critique_reasks_inconsistent_samples(self) -> None:
        bad = Verdict(verdict="ADJUST", category="none", adjustment="")
        replies = iter([bad, READY, READY, READY])
        with mock.patch.object(critique_mod.gemini, "generate", side_effect=lambda *a, **k: next(replies)):
            img = Image.new("RGB", (4, 4))
            v = critique_mod.critique(img, img, make_step(), n=1, client=object())
        self.assertEqual(v.verdict, "READY")


class StepMachineTest(unittest.TestCase):
    def test_ready_advances_then_completes(self) -> None:
        m = StepMachine([make_step(1), make_step(2)])
        self.assertEqual(m.submit(READY), "advanced")
        self.assertEqual(m.current.index, 2)
        self.assertEqual(m.submit(READY), "complete")
        self.assertIsNone(m.current)

    def test_stuck_after_three_tries_then_skip(self) -> None:
        m = StepMachine([make_step(1), make_step(2)])
        self.assertEqual(m.submit(adjust("value")), "retry")
        self.assertEqual(m.submit(adjust("value")), "retry")
        self.assertEqual(m.submit(adjust("value")), "stuck")
        with self.assertRaises(RuntimeError):
            m.submit(READY)
        self.assertEqual(m.skip(), "advanced")
        self.assertEqual((m.current.index, m.state["tries"]), (2, 0))

    def test_ready_resets_tries_and_state_round_trips(self) -> None:
        steps = [make_step(1), make_step(2)]
        m = StepMachine(steps)
        m.submit(adjust("coverage"))
        m.submit(READY)
        m2 = StepMachine.from_dict(steps, dict(m.to_dict()))
        self.assertEqual((m2.current.index, m2.state["tries"]), (2, 0))


class MockLayersAndPlannerTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        rng = np.random.default_rng(0)
        gradient = np.tile(np.linspace(0, 255, 120, dtype=np.uint8), (80, 1))
        noise = rng.integers(0, 20, gradient.shape, dtype=np.uint8)
        self.ref = self.dir / "ref.png"
        Image.fromarray(np.stack([gradient, gradient, noise + gradient // 2], axis=2).astype(np.uint8)).save(self.ref)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_mock_masks_satisfy_track3_contract(self) -> None:
        make_value_masks(self.ref, self.dir / "scene", size=(120, 80))
        report = validate_scene(self.dir / "scene")
        self.assertTrue(report.passed, report.errors)

    def test_planner_retries_on_invalid_then_measures_rgb(self) -> None:
        scene = self.dir / "scene"
        masks = make_value_masks(self.ref, scene, size=(120, 80))
        ids = [m.stem for m in masks]
        bad = PlanDraft(steps=[draft_step(i, "unobtainium") for i in ids])
        good = PlanDraft(steps=[draft_step(i) for i in reversed(ids)])
        replies = iter([bad, good])
        with mock.patch.object(planner.gemini, "generate", side_effect=lambda *a, **k: next(replies)) as gen:
            steps = planner.plan(self.ref, masks, scene_dir=scene, client=object())
        self.assertEqual(gen.call_count, 2)
        self.assertIn("invalid", gen.call_args_list[1].args[1][0])  # feedback reached the retry prompt
        self.assertEqual([s.index for s in steps], [1, 2, 3, 4, 5])
        self.assertEqual(steps[0].mask_path, f"layers/{ids[-1]}.png")  # Gemini's order is kept
        self.assertGreater(sum(steps[0].target_rgb), sum(steps[-1].target_rgb))  # reversed order: lightest first
        self.assertNotEqual(steps[0].target_rgb, steps[-1].target_rgb)

    def test_replan_revises_remaining_steps_over_the_same_masks(self) -> None:
        scene = self.dir / "scene"
        masks = make_value_masks(self.ref, scene, size=(120, 80))
        ids = [m.stem for m in masks]
        first = PlanDraft(steps=[draft_step(i) for i in ids])
        with mock.patch.object(planner.gemini, "generate", return_value=first):
            steps = planner.plan(self.ref, masks, scene_dir=scene, client=object())
        stuck_pos = 2
        remaining_ids = [Path(s.mask_path).stem for s in steps[stuck_pos:]]
        bad = PlanDraft(steps=[draft_step(remaining_ids[0])])                      # drops two masks
        good = PlanDraft(steps=[draft_step(i, "burnt umber") for i in reversed(remaining_ids)])
        replies = iter([bad, good])
        history = [{"step": 3, "verdict": "ADJUST", "category": "value", "adjustment": "Lighten it."},
                   {"step": 1, "verdict": "ADJUST", "category": "coverage", "adjustment": "unrelated"}]
        with mock.patch.object(planner.gemini, "generate", side_effect=lambda *a, **k: next(replies)) as gen:
            new = planner.replan(self.ref, self.ref, steps, stuck_pos, history, scene_dir=scene, client=object())
        self.assertEqual(gen.call_count, 2)                                        # invalid first, then fixed
        retry_prompt = gen.call_args_list[1].args[1][0]
        self.assertIn("invalid", retry_prompt)
        self.assertIn("Lighten it.", retry_prompt)                                 # this step's corrections are shown
        self.assertNotIn("unrelated", retry_prompt)                                # other steps' are not
        self.assertEqual([s.index for s in new], [3, 4, 5])                        # continues after finished steps
        self.assertEqual(sorted(Path(s.mask_path).stem for s in new), sorted(remaining_ids))
        self.assertEqual(new[0].mask_path, f"layers/{remaining_ids[-1]}.png")

    def test_planner_gives_up_after_max_tries(self) -> None:
        masks = make_value_masks(self.ref, self.dir / "scene", size=(120, 80))
        bad = PlanDraft(steps=[draft_step(m.stem, "unobtainium") for m in masks])
        with mock.patch.object(planner.gemini, "generate", return_value=bad):
            with self.assertRaises(ValueError):
                planner.plan(self.ref, masks, client=object(), max_tries=2)


if __name__ == "__main__":
    unittest.main()
