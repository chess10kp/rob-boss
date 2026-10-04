"""Track 2 CLI, runs with no hardware.

  python -m track2.demo plan fixtures/spike_c/bobross-sunset.jpg --out scenes/sunset
  python -m track2.demo critique scenes/sunset --ref REF --capture IMG --step 1
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from track2.critique import critique
from track2.mock_layers import make_value_masks
from track2.planner import plan
from track2.schema import Step


def cmd_plan(args) -> None:
    out = Path(args.out)
    layers = out / "layers"
    masks = sorted(layers.glob("*.png")) if layers.is_dir() else []
    if not masks:
        masks = make_value_masks(Path(args.ref), out)
        print(f"no layers found; wrote {len(masks)} mock value masks to {layers}")
    steps = plan(args.ref, masks, scene_dir=out)
    (out / "plan.json").write_text(json.dumps([s.model_dump() for s in steps], indent=2))
    for s in steps:
        mix = " + ".join(f"{m.parts} {m.pigment}" for m in s.mix)
        print(f"{s.index}. {s.name}  [{s.mask_path}]  rgb={s.target_rgb}\n"
              f"   mix: {mix} | {s.brush} | {s.technique} ({s.stroke_dir_deg} deg)\n"
              f"   success: {s.success}")


def cmd_critique(args) -> None:
    steps = [Step(**d) for d in json.loads((Path(args.scene) / "plan.json").read_text())]
    step = next(s for s in steps if s.index == args.step)
    v = critique(args.ref, args.capture, step, mask=Path(args.scene) / step.mask_path)
    print(json.dumps(v.model_dump(), indent=2))


def main() -> None:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(required=True)
    p = sub.add_parser("plan")
    p.add_argument("ref")
    p.add_argument("--out", required=True)
    p.set_defaults(fn=cmd_plan)
    c = sub.add_parser("critique")
    c.add_argument("scene")
    c.add_argument("--ref", required=True)
    c.add_argument("--capture", required=True)
    c.add_argument("--step", type=int, required=True)
    c.set_defaults(fn=cmd_critique)
    args = ap.parse_args()
    args.fn(args)


if __name__ == "__main__":
    main()
