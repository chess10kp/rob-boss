"""Simulated hands-free session on a planned scene (no hardware).

  python -m track2.watch_demo scenes/sunset fixtures/spike_c/bobross-sunset.jpg [--offline]

Plays step 1 as: paint part of it, pause, repaint too dark, pause, repaint correctly.
Gemini is only called to confirm the finished step (--offline replaces it with READY).
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
from PIL import Image

from track2.cv import DEFAULT_BARE_RGB
from track2.machine import StepMachine
from track2.schema import Step, Verdict
from track2.simulate import SimFeed, run
from track2.watcher import Watcher


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("scene")
    ap.add_argument("ref")
    ap.add_argument("--offline", action="store_true")
    args = ap.parse_args()
    scene = Path(args.scene)
    steps = [Step(**d) for d in json.loads((scene / "plan.json").read_text())]
    ref = np.asarray(Image.open(args.ref).convert("RGB").resize((768, 512)))
    mask = np.asarray(Image.open(scene / steps[0].mask_path).convert("L"))
    region = mask > 127
    rows = np.mgrid[0:512, 0:768][0]

    def canvas(frac: float = 1.0, scale: float = 1.0) -> np.ndarray:
        out = np.full_like(ref, DEFAULT_BARE_RGB)
        sel = region & (rows < 512 * frac)
        out[sel] = np.clip(ref[sel].astype(np.float32) * scale, 0, 255).astype(np.uint8)
        return out

    feed = SimFeed((768, 512))
    crit = (lambda c, s, m: Verdict(verdict="READY", category="none", adjustment="")) if args.offline else None
    watcher = Watcher(StepMachine(steps), ref, scene, feed.capture, crit)
    print(f"step 1: {steps[0].name}")
    script = [
        (canvas(0.3), 2.0, 5.0),    # painter works, pauses 5s: still just progress
        (canvas(0.3), 0.0, 8.0),    # keeps pausing: now it is a mistake (unpainted area)
        (canvas(1.0, 0.5), 3.0, 8.0),   # repaints everything too dark
        (canvas(1.0), 3.0, 10.0),   # fixes it
    ]
    t = 0.0
    for part in script:
        for now, ev in run(watcher, feed, [part], t0=t):
            where = "" if ev.missing is None else f"  [{int(ev.missing.sum())} bare px to project]"
            text = ev.verdict.adjustment or "step complete"
            print(f"  t={now:5.1f}s  {ev.kind:10} via {ev.source:6} {ev.verdict.category}: {text}{where}")
        t += part[1] + part[2]
    print(f"machine: status={watcher.machine.status}, current={getattr(watcher.machine.current, 'index', None)}")


if __name__ == "__main__":
    main()
