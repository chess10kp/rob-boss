"""
Gate 3 rehearsal: fixed reference, pre-mixed paints, locked lighting, and a planted mistake
so the adaptive critique visibly fires. The demo is fixed in config/rehearsal.json.

    uv run python -m rehearsal dry-run           # whole lesson, simulated camera, planted mistake
    uv run python -m rehearsal sheet             # run sheet: paints to pre-mix, the planted mistake
    uv run python -m rehearsal lock              # at the rig: record the lighting as it should be
    uv run python -m rehearsal check             # at the rig, before each run: is everything as locked?
    uv run python -m track1.show                 # then the real run (upload screen)

dry-run plays the painter on a simulated camera: each step is painted half way (the watcher
must stay quiet: that is progress), then finished (it must advance). On the planted step the
painter first paints it wrong (too dark / too light / patchy), the watcher must correct it,
then the painter fixes it and it must advance. Every expectation is checked and the run
fails loudly if one is missed. --gemini lets Gemini confirm each step (about 3 calls a step);
otherwise confirmation is skipped. The simulated camera is exact, so a dry-run pass proves the
software flow and the planted mistake, not the rig thresholds: that is what lock/check and a
real rehearsal are for.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

import cv2
import numpy as np
from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
CONFIG = ROOT / "config" / "rehearsal.json"
LIGHTING = ROOT / "config" / "rehearsal_lighting.json"


def load_config() -> dict:
    cfg = json.loads(CONFIG.read_text())
    cfg["reference"], cfg["scene"] = ROOT / cfg["reference"], ROOT / cfg["scene"]
    return cfg


def load_lesson(scene: Path):
    from track2.schema import Step
    path = scene / "plan.json"
    if not path.exists():
        raise SystemExit(f"no lesson in {scene}: run uv run python -m track1.show once, or track2.demo plan")
    return [Step(**d) for d in json.loads(path.read_text())]


# ---------------------------------------------------------------- dry run

def planted_canvas(kind: str, before: np.ndarray, after: np.ndarray, change: np.ndarray) -> np.ndarray:
    """The step painted wrong: what the planted mistake looks like on the canvas."""
    out = before.copy()
    if kind == "too_dark":
        out[change] = (after[change].astype(np.float32) * 0.55).astype(np.uint8)
    elif kind == "too_light":
        a = after[change].astype(np.float32)
        out[change] = (a + (255 - a) * 0.6).astype(np.uint8)
    elif kind == "patchy":                       # left half of the region left unpainted
        cols = np.nonzero(change.any(axis=0))[0]
        left = np.zeros_like(change)
        left[:, cols.min():(cols.min() + cols.max()) // 2] = True
        sel = change & ~left
        out[sel] = after[sel]
    else:
        raise ValueError(f"unknown planted mistake {kind!r}: too_dark | too_light | patchy")
    return out


EXPECT = {"too_dark": "value", "too_light": "value", "patchy": "coverage"}


def dry_run(args) -> int:
    from track2 import cv as cvmod
    from track2.machine import StepMachine
    from track2.schema import Verdict
    from track2.simulate import SimFeed, run
    from track2.watcher import STACK_BARE, Watcher

    cfg = load_config()
    planted = cfg["planted"]
    step_no, kind = args.step or planted["step"], args.kind or planted["kind"]
    lesson = load_lesson(cfg["scene"])
    ref = np.asarray(Image.open(cfg["reference"]).convert("RGB"))
    machine = StepMachine(lesson)
    crit = None if args.gemini else (lambda c, s, m: Verdict(verdict="READY", category="none", adjustment=""))
    h, w = ref.shape[:2]
    feed = SimFeed((w, h))
    feed.set(np.full((h, w, 3), STACK_BARE, np.uint8))
    watcher = Watcher(machine, ref, cfg["scene"], feed.capture, crit)
    watcher.calibrate(feed.capture())

    print(f"dry run: {cfg['reference'].name}, {len(lesson)} steps, planted mistake: step {step_no} {kind}"
          f" ({'Gemini confirms' if args.gemini else 'no Gemini'})\n")
    problems, gemini_only, t = [], [], 0.0

    def play(canvas, hand_s, still_s):
        nonlocal t
        evs = run(watcher, feed, [(canvas, hand_s, still_s)], t0=t)
        t += hand_s + still_s
        return evs

    def report(label, evs):
        for now, ev in evs:
            text = ev.verdict.adjustment if ev.verdict and ev.verdict.adjustment else ""
            print(f"    t={now:6.1f}s  {label:12} -> {ev.kind} ({ev.source}) {text}")
        if not evs:
            print(f"    {'':9}{label:12} -> (quiet)")

    for step in lesson:
        target, before, mask = watcher._target(step)
        change = mask > 127
        start = feed.canvas.copy()
        after = target.copy()                     # finished: the canvas looks like Track 3's step frame
        checkable = cvmod.measure(after, target, mask, bare_image=start, before_rgb=before).checkable
        print(f"  step {step.index}: {step.name}  ({change.mean() * 100:.0f}% of the canvas changes"
              + ("" if checkable else "; too fine for the camera check, only Gemini judges it") + ")")
        if not checkable:
            gemini_only.append(step.index)
            if step.index == step_no:
                problems.append(f"step {step.index}: the planted mistake is on a step the camera cannot check")

        # Half painted, then a short pause: progress, the watcher must not interrupt.
        rows = np.nonzero(change.any(axis=1))[0] if checkable else np.array([])
        half = start.copy()
        if rows.size:                        # the top half of the region's pixels, in reading order
            idx = np.flatnonzero(change)[: int(change.sum()) // 2]
            half.reshape(-1, 3)[idx] = after.reshape(-1, 3)[idx]
        evs = play(half, 2.0, 4.0)
        report("half done", evs)
        if any(ev.kind in ("advanced", "complete") for _, ev in evs):
            if checkable:
                problems.append(f"step {step.index}: advanced while only half painted")
            if machine.status == "complete":
                break
            continue                         # the camera can't see this step: confirmation alone moved on

        if step.index == step_no and checkable:
            wrong = planted_canvas(kind, start, after, change)
            evs = play(wrong, 2.0, 12.0)
            report(f"planted {kind}", evs)
            fired = [ev for _, ev in evs if ev.kind == "correction"]
            if not fired:
                problems.append(f"step {step.index}: planted {kind} mistake was not corrected")
            elif fired[0].verdict.category != EXPECT[kind]:
                problems.append(f"step {step.index}: planted {kind} drew a {fired[0].verdict.category} "
                                f"correction, expected {EXPECT[kind]}")
            elif any(ev.kind in ("advanced", "complete") for _, ev in evs):
                problems.append(f"step {step.index}: advanced past the planted mistake")
            else:
                where = fired[0].off_value if fired[0].off_value is not None else fired[0].missing
                print(f"    {'':9}{'':12}    projected correction covers "
                      f"{0 if where is None else where.mean() * 100:.0f}% of the canvas")

        evs = play(after, 2.0, 6.0)
        report("finished", evs)
        done = [ev for _, ev in evs if ev.kind in ("advanced", "complete")]
        if not done:
            problems.append(f"step {step.index}: did not advance when finished correctly")
        if machine.status == "complete":
            break

    print(f"\nsession: {machine.status}, {len(machine.state['history'])} verdicts recorded")
    if gemini_only:
        print(f"camera can't check steps {gemini_only} (too fine): "
              + ("Gemini judged them." if args.gemini else "they pass on confirmation alone; with --gemini, Gemini judges them."))
    if machine.status != "complete":
        problems.append(f"lesson did not complete (status {machine.status})")
    if problems:
        print("\nREHEARSAL DRY RUN FAILED:\n  - " + "\n  - ".join(problems))
        return 1
    print("REHEARSAL DRY RUN PASSED: every step advanced only when finished, and the planted mistake was caught.")
    return 0


# ---------------------------------------------------------------- run sheet

PIGMENT_HEX = {"titanium white": "#f5f5f0", "ivory black": "#1e1e1e", "ultramarine blue": "#1e32a0",
               "cadmium yellow": "#fac814", "cadmium red": "#c81e1e", "burnt umber": "#5a3723"}


def sheet(args) -> int:
    cfg = load_config()
    lesson = load_lesson(cfg["scene"])
    p = cfg["planted"]
    lines = [f"# Rehearsal run sheet: {cfg['reference'].name}", "",
             "## Before the run", "",
             "- [ ] Paper: plain letter sheet, taped in the saved position (or run show with --detect)",
             "- [ ] Lights: as locked. Run `uv run python -m rehearsal check` and get PASS",
             "- [ ] Projector lamp on; LightGuide running",
             f"- [ ] {len(lesson)} cups pre-mixed and labelled 1 to {len(lesson)} (below), brushes clean",
             "- [ ] Start: `uv run python -m track1.show`, upload any image, keep hands off the paper "
             "until 'Calibrate the bare canvas' is ticked", "",
             "## Paints to pre-mix", "",
             "| Cup | Step | Mix (parts) | Brush |", "|---|---|---|---|"]
    for s in lesson:
        mix = " + ".join(f"{m.parts} {m.pigment}" for m in s.mix)
        lines.append(f"| {s.index} | {s.name} | {mix} | {s.brush} |")
    planted = next(s for s in lesson if s.index == p["step"])
    lines += ["", "## The planted mistake", "",
              f"On **step {planted.index} ({planted.name})**: {p['how']}", "",
              f"Expected: once your hand has been out of the region for 2.5 s, the system flags it as "
              f"**{EXPECT[p['kind']]}**, blinks the wrong areas on the paper, and the control page says what "
              "to change. Fix it as told; the step should then advance on its own.", "",
              "If it does not fire: note the time and what the page said, press SPACE to carry on.", "",
              "Before changing the planted step, run `uv run python -m rehearsal dry-run --step N`. Known limits:",
              "- *Too light* over a pale earlier layer looks unpainted to the camera: it gets a 'fill in' "
              "correction, not 'too light'. Plant *too dark*.",
              "- Dark warm mistakes (brown, dark orange) can look like skin: the watcher waits for the 'hand' "
              "to leave and says nothing. Plant on a blue, green or grey step.",
              "- Near-black steps (the pines) cannot be painted too dark.", ""]
    text = "\n".join(lines)
    out = cfg["scene"] / "rehearsal_sheet.md"
    out.write_text(text, encoding="utf-8")
    print(text)
    print(f"\n(saved to {out})")
    return 0


# ---------------------------------------------------------------- lighting lock / check

def measure_lighting(rig) -> dict:
    """Ambient (projector black) and flash-lit levels on the paper, as seen by the camera."""
    from track1 import geometry
    rig._require_quad()
    size = rig.canvas_size(512)
    rig.clear()
    time.sleep(rig.settings["flash_settle_s"] + 0.3)
    ambient = cv2.cvtColor(geometry.rectify(rig.frame(), rig.quad_cam, size), cv2.COLOR_BGR2GRAY)
    flash = cv2.cvtColor(rig.capture_canvas(512), cv2.COLOR_BGR2GRAY)
    rig.clear()
    h, w = flash.shape
    centre, edge = flash[h // 3: 2 * h // 3, w // 3: 2 * w // 3], np.concatenate([flash[: h // 8].ravel(), flash[-h // 8:].ravel()])
    return {"ambient_mean": float(ambient.mean()), "flash_mean": float(flash.mean()),
            "flash_p5": float(np.percentile(flash, 5)), "flash_p95": float(np.percentile(flash, 95)),
            "clipped_frac": float((flash >= 250).mean()), "falloff": float(edge.mean() / max(1.0, centre.mean())),
            "time": time.strftime("%Y-%m-%d %H:%M:%S")}


def lock(args) -> int:
    from track1 import Rig
    rig = Rig()
    try:
        m = measure_lighting(rig)
    finally:
        rig.close()
    LIGHTING.write_text(json.dumps(m, indent=2))
    print(json.dumps(m, indent=2))
    lamp = m["flash_mean"] - m["ambient_mean"]
    print(f"\nlocked to {LIGHTING}" + ("" if lamp > 30 else
          f"\nWARNING: the flash only adds {lamp:.0f} grey levels over ambient: is the projector lamp on?"))
    return 0


def check(args) -> int:
    from track1 import Rig
    from track1.rig import QUAD_PATH
    cfg = load_config()
    results = []

    def item(ok, name, detail):
        results.append(ok)
        print(f"  [{'PASS' if ok else 'FAIL'}] {name}: {detail}")

    print("rehearsal check")
    item(cfg["reference"].exists(), "reference", cfg["reference"].name)
    plan = cfg["scene"] / "plan.json"
    item(plan.exists(), "lesson", f"{len(json.loads(plan.read_text()))} steps" if plan.exists() else "no plan.json")
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")
    item(bool(os.environ.get("GEMINI_API_KEY")), "Gemini key", "set in .env" if os.environ.get("GEMINI_API_KEY") else "missing")
    item(QUAD_PATH.exists(), "paper corners", "saved" if QUAD_PATH.exists() else "run track1.show --detect")
    if not LIGHTING.exists():
        item(False, "lighting", "not locked yet: run uv run python -m rehearsal lock")
        return 1
    base = json.loads(LIGHTING.read_text())
    try:
        rig = Rig()
    except Exception as e:
        item(False, "rig", f"{type(e).__name__}: {e}")
        return 1
    try:
        m = measure_lighting(rig)
    finally:
        rig.close()
    lamp = m["flash_mean"] - m["ambient_mean"]
    item(lamp > 30, "projector lamp", f"flash adds {lamp:.0f} grey levels over ambient")
    da = m["ambient_mean"] - base["ambient_mean"]
    item(abs(da) <= max(6.0, 0.15 * base["ambient_mean"]), "room light",
         f"ambient {m['ambient_mean']:.0f} vs locked {base['ambient_mean']:.0f} ({da:+.0f})")
    df = m["flash_mean"] - base["flash_mean"]
    item(abs(df) <= max(8.0, 0.08 * base["flash_mean"]), "paper under flash",
         f"{m['flash_mean']:.0f} vs locked {base['flash_mean']:.0f} ({df:+.0f}); a big change means the paper "
         "moved, something is on it, or the lights changed")
    item(m["clipped_frac"] < 0.02, "no glare", f"{m['clipped_frac'] * 100:.1f}% of the paper clipped white")
    item(abs(m["falloff"] - base["falloff"]) < 0.08, "light even as locked",
         f"edge/centre {m['falloff']:.2f} vs locked {base['falloff']:.2f}")
    ok = all(results)
    print("\nREADY TO REHEARSE" if ok else "\nNOT READY: fix the FAIL lines above")
    return 0 if ok else 1


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(required=True)
    d = sub.add_parser("dry-run", help="simulated full lesson with the planted mistake")
    d.add_argument("--gemini", action="store_true", help="let Gemini confirm each step (API calls)")
    d.add_argument("--step", type=int, help="plant the mistake on this step instead")
    d.add_argument("--kind", choices=list(EXPECT), help="plant this kind of mistake instead")
    d.set_defaults(fn=dry_run)
    sub.add_parser("sheet", help="print and save the run sheet").set_defaults(fn=sheet)
    sub.add_parser("lock", help="record the rig lighting").set_defaults(fn=lock)
    sub.add_parser("check", help="pre-run check against the locked lighting").set_defaults(fn=check)
    args = ap.parse_args()
    sys.exit(args.fn(args))


if __name__ == "__main__":
    main()
