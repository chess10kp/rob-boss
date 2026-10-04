"""
One command: reference image -> Track 3 layers -> projected step by step on the canvas.

    uv run python -m track1.show                     # upload screen, then the whole hands-free run
    uv run python -m track1.show fixtures/spike_c/bobross-sunset.jpg
    uv run python -m track1.show my_painting.jpg --detect        # click the paper's corners first
    uv run python -m track1.show my_painting.jpg --remote        # GIMP server (5-band partition)
    uv run python -m track1.show my_painting.jpg --projector direct   # if a window covers LightGuide

1. Decompose. By default track3.layers (the layer stack, run locally, ~40 s) into
   scenes/<image name>-layers/; reused on later runs unless --redo. With --remote the image
   goes to the GIMP API instead (REMOTE_API.md; token in ~/.cache/rob-boss/agent-api-token)
   and comes back as the older five-band partition.
2. Project. Each step shows track3's step frame (steps/NN_*.png): the whole picture as it
   stands after that step, colour-corrected for the projector. --layers instead lights
   only that step's layer area of it (see track1/project_scene.py).
3. Step through it on the control page, http://localhost:8765/ (opened in the browser;
   track1/panel.py): the projected step, Track 2's mix / brush / technique for it, the lesson
   list, and buttons. Keys on the page:
       SPACE / right arrow / N  next step       left arrow / B  previous step
       O  outline + stroke arrows on/off        ESC / Q  quit (or Ctrl+C here)

With no image the page opens on an upload screen. The upload is saved to scenes/uploads/, but
for the demo the run uses DEMO_IMAGE (the Bob Ross sunset, whose layers and plan are cached);
--use-upload runs the uploaded image itself (track3.layers ~40 s, then Gemini plans it). Then
it goes through decompose, plan, rig, canvas and bare-canvas calibration, shown as stages on
the page, and into the --watch loop below.

--watch (Gate 3, join 3: Track 1 -> Track 2) runs the hands-free loop instead. Track 2's
watcher sees the canvas through the camera: once the step's region has been still for
2.5 s after the hand leaves it, it measures coverage and value locally and, when the step
looks done, asks Gemini to confirm. READY advances to the next step; a correction blinks
the areas to fix (bare or off-value) and is shown on the control page. Needs plan.json
(track2.demo plan). SPACE forces the step done, K skips a stuck step, ESC quits.
    uv run python -m track1.show my_painting.jpg --watch
"""

from __future__ import annotations

import argparse
import json
import time
import webbrowser
from pathlib import Path

import numpy as np

from . import Rig
from .panel import Panel
from .project_scene import scene_steps, step_images, step_overlay

ROOT = Path(__file__).resolve().parent.parent
DEMO_IMAGE = ROOT / "fixtures" / "spike_c" / "bobross-sunset.jpg"   # what an upload runs (see --use-upload)
UPLOADS = ROOT / "scenes" / "uploads"


def decompose(image: Path, remote: bool, redo: bool) -> Path:
    if remote:
        from track3.remote import Remote
        client = Remote()
        print(f"sending {image.name} to the GIMP API at {client.base} ...")
        public = client.process(image)
        scene = client.download_scene(public)
        print(f"  job {public['job_id']}: {len(public['steps'])} steps -> {scene}")
        return scene
    scene = ROOT / "scenes" / f"{image.stem}-layers"
    if (scene / "report.json").exists() and not redo:
        print(f"using existing layers in {scene} (--redo to decompose again)")
        return scene
    from track3.layers import decompose as layers_decompose
    print(f"decomposing {image.name} with track3.layers (about 40 s) ...")
    t = time.monotonic()
    report = layers_decompose(image.resolve(), scene)
    print(f"  {report['stage_count']} steps in {time.monotonic() - t:.0f} s -> {scene}")
    return scene


def load_plan(scene: Path) -> dict[int, dict]:
    """Track 2's lesson for this scene (plan.json from track2.demo plan), by step index."""
    path = scene / "plan.json"
    return {s["index"]: s for s in json.loads(path.read_text())} if path.exists() else {}


def ensure_plan(image: Path, scene: Path, redo: bool) -> tuple[dict[int, dict], str]:
    """Track 2's lesson for the scene: plan.json if it is there (unless redo), else ask Gemini."""
    if (scene / "plan.json").exists() and not redo:
        plan = load_plan(scene)
        return plan, f"{len(plan)} steps (saved plan; --replan to ask Gemini again)"
    from track2.demo import scene_masks
    from track2.planner import plan as make_plan
    steps = make_plan(image, scene_masks(image, scene, mock=False), scene_dir=scene)
    (scene / "plan.json").write_text(json.dumps([s.model_dump() for s in steps], indent=2))
    return load_plan(scene), f"{len(steps)} steps written by Gemini"


def receive_upload(panel: Panel, args) -> Path | None:
    """Upload screen. The upload is saved; the run uses DEMO_IMAGE unless --use-upload."""
    got = panel.wait_upload()
    if got is None:
        return None
    name, data = got
    UPLOADS.mkdir(parents=True, exist_ok=True)
    saved = UPLOADS / f"{time.strftime('%Y%m%d-%H%M%S')}_{Path(name).stem[:40]}{Path(name).suffix.lower() or '.jpg'}"
    saved.write_bytes(data)
    print(f"upload saved to {saved}")
    if args.use_upload:
        return saved
    print(f"running the demo image {DEMO_IMAGE.name} (--use-upload to run the uploaded image)")
    return DEMO_IMAGE


def open_panel(args) -> Panel:
    panel = Panel(args.panel_host, args.port).start()
    print(f"control page: {panel.url}")
    if not args.no_browser:
        webbrowser.open(panel.url)
    return panel


def run_stage(panel: Panel, i: int, fn, running: str = ""):
    """Run one pipeline stage, showing it on the page. fn() returns its result and a detail line."""
    panel.stage(i, "running", running)
    try:
        result, detail = fn()
    except Exception as e:
        panel.stage(i, "failed", f"{type(e).__name__}: {e}")
        raise
    panel.stage(i, "done", detail)
    return result


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("image", type=Path, nargs="?",
                    help="reference image (jpg/png); leave out for the upload screen + hands-free run")
    ap.add_argument("--remote", action="store_true", help="decompose on the GIMP server instead of locally")
    ap.add_argument("--redo", action="store_true", help="decompose again even if layers exist")
    ap.add_argument("--detect", action="store_true", help="find (or click) the canvas before projecting")
    ap.add_argument("--any-shape", action="store_true", help="with --detect: don't require letter-paper proportions")
    ap.add_argument("--projector", choices=["lightguide", "direct"], help="override rig_settings")
    ap.add_argument("--outline", action="store_true", help="start with outlines and stroke arrows on")
    ap.add_argument("--layers", action="store_true", help="light only each step's layer area, not the whole step")
    ap.add_argument("--watch", action="store_true", help="hands-free: advance when the watcher says the step is done")
    ap.add_argument("--offline", action="store_true", help="with --watch: skip Gemini, accept any step CV finds done")
    ap.add_argument("--seconds", type=float, default=0, help="quit after this long (for unattended tests)")
    ap.add_argument("--port", type=int, default=8765, help="control page port")
    ap.add_argument("--panel-host", default="127.0.0.1", help="0.0.0.0 to open the page from another device")
    ap.add_argument("--no-browser", action="store_true", help="don't open the control page automatically")
    ap.add_argument("--replan", action="store_true", help="ask Gemini for a new plan.json even if one exists")
    ap.add_argument("--use-upload", action="store_true",
                    help=f"upload screen: run the uploaded image instead of {DEMO_IMAGE.name}")
    args = ap.parse_args()
    if args.image is not None and not args.image.exists():
        ap.error(f"{args.image} not found")

    panel, rig = open_panel(args), None
    try:
        names = ["Decompose into layers (Track 3)", "Plan the lesson (Track 2)",
                 "Connect projector and camera (Track 1)", "Find the canvas"]
        if args.image is None:                       # upload screen, then the whole hands-free flow
            image = receive_upload(panel, args)
            if image is None:
                return
            args.image, args.watch = image, True
            names = ["Upload"] + names
        if args.watch:
            names.append("Calibrate the bare canvas")
        panel.stages(names)
        k = 0
        if names[0] == "Upload":
            panel.stage(0, "done", f"{args.image.name}" + ("" if args.use_upload else " (demo image)"))
            k = 1

        scene = run_stage(panel, k, lambda: (lambda s: (s, s.name))(decompose(args.image, args.remote, args.redo)),
                          "splitting the picture into paint layers")
        steps = scene_steps(scene)
        images = step_images(scene, steps, mode="layers" if args.layers else "steps")
        try:
            plan = run_stage(panel, k + 1, lambda: ensure_plan(args.image, scene, args.replan),
                             "Gemini is writing the mixes and techniques")
        except Exception as e:
            if args.watch:
                raise
            print(f"no lesson plan ({e}); projecting without one")
            plan = {}
        print(f"lesson: {len(plan)} planned steps from {scene / 'plan.json'}" if plan else "lesson: no plan.json")

        def connect():
            r = Rig(args.projector)
            r.frame()
            return r, f"{r.settings['projector']} projector, camera {r.cam_size[0]}x{r.cam_size[1]}"
        rig = run_stage(panel, k + 2, connect, "opening the camera")
        if args.any_shape:
            rig.settings["canvas_mm"] = None

        def canvas():
            if args.detect or rig.quad_cam is None:
                rig.detect_canvas()
                return None, "found the paper"
            return None, "using the saved paper corners (--detect to find them again)"
        run_stage(panel, k + 3, canvas, "looking for the paper")

        if args.watch:
            watch(rig, panel, args, scene, steps, images, plan, calibrate_stage=k + 4)
            return
        lessons = [plan[k] for k in sorted(plan)]
        i, outline, shown, t0 = 0, args.outline, None, time.monotonic()
        while True:
            if shown != (i, outline):
                rig.project_overlay(*step_overlay(steps[i], images[i], fill_only=not outline))
                panel.update(step=steps[i], index=i + 1, count=len(steps), outline=outline,
                             lesson=plan.get(i + 1), lessons=lessons,
                             preview=images[i] if shown is None or shown[0] != i else None)
                shown = (i, outline)
            key = panel.key(0.05)
            if key == "quit" or (args.seconds and time.monotonic() - t0 > args.seconds):
                break
            if key == "next":
                i = min(i + 1, len(steps) - 1)
            elif key == "back":
                i = max(i - 1, 0)
            elif key == "outline":
                outline = not outline
    except KeyboardInterrupt:
        pass
    except Exception as e:                      # leave the failed stage on the page until quit
        print(f"failed: {type(e).__name__}: {e}\n(the control page shows where; Cancel or Ctrl+C to exit)")
        try:
            while panel.key(0.5) != "quit":
                pass
        except KeyboardInterrupt:
            pass
        raise
    finally:
        if rig:
            rig.clear()
            rig.close()
        panel.close()


def watch(rig, panel, args, scene, steps, images, plan, calibrate_stage=None):
    """The hands-free loop: Track 1 camera -> Track 2 watcher -> what Track 1 projects."""
    from PIL import Image
    from track2.machine import StepMachine
    from track2.schema import Step, Verdict
    from track2.watcher import Watcher, default_replan
    from . import geometry

    if not plan:
        raise SystemExit(f"--watch needs a lesson: uv run python -m track2.demo plan {args.image} --out {scene}")
    lesson = [Step(**plan[k]) for k in sorted(plan)]
    ref = np.asarray(Image.open(args.image).convert("RGB"))
    machine = StepMachine(lesson)
    peek_size = (384, round(384 * ref.shape[0] / ref.shape[1]))

    def peek():        # cheap: no flash, the projected step stays up
        return geometry.rectify(rig.frame(), rig.quad_cam, peek_size)

    def capture():     # flash-lit, rectified to canvas space
        return rig.capture_canvas()

    critique = (lambda canvas, step, mask: Verdict(verdict="READY", category="none", adjustment="")) \
        if args.offline else None
    w = Watcher(machine, ref, scene, capture, critique,
                replan_fn=None if args.offline else default_replan(ref, machine, scene))
    print("calibrating bare canvas from a capture of the paper ...")
    if calibrate_stage is not None:
        run_stage(panel, calibrate_stage, lambda: (w.calibrate(capture()), "flash-lit capture of the blank paper"),
                  "keep the paper blank and hands clear")
    else:
        w.calibrate(capture())

    outline, shown, status = args.outline, None, "watching: paint the region; I check 2.5 s after your hand leaves it"
    t0 = time.monotonic()
    while machine.status != "complete":
        i = machine.state["current"]
        if shown != (i, outline, status):
            if shown is None or shown[:2] != (i, outline):
                rig.project_overlay(*step_overlay(steps[i], images[i], fill_only=not outline))
            panel.update(step=steps[i], index=i + 1, count=len(steps), outline=outline, watching=True,
                         lesson=machine.steps[i].model_dump() if i < len(machine.steps) else None,
                         lessons=[s.model_dump() for s in machine.steps], status=status,
                         preview=images[i] if shown is None or shown[0] != i else None)
            shown = (i, outline, status)

        now = time.monotonic() - t0
        ev = w.tick(peek(), now) if machine.status == "active" else None
        if ev is not None:
            text = ev.verdict.adjustment if ev.verdict and ev.verdict.adjustment else ""
            print(f"[{now:6.1f}s] step {ev.step_index}: {ev.kind} ({ev.source}) {text}", flush=True)
            if ev.kind in ("advanced", "complete"):
                status = "step complete - next step" if ev.kind == "advanced" else "painting complete"
            elif ev.kind == "replanned":
                status = "re-planned: " + (ev.new_steps[0].technique if ev.new_steps else "")
            elif ev.kind == "stuck":
                status = f"stuck: {text}  (K to skip)"
            else:
                status = f"fix: {text}"
                where = ev.missing if ev.missing is not None else ev.off_value
                if where is not None and where.any():
                    rig.flash_correction(np.where(where, 255, 0).astype(np.uint8), times=3)

        key = panel.key(0.2)
        if key == "quit":
            break
        if args.seconds and now > args.seconds:
            print("time limit reached")
            break
        if key == "next" and machine.status == "active":          # painter says it's done
            machine.submit(Verdict(verdict="READY", category="none", adjustment=""))
            w.rebase(capture())       # the next step is judged from the canvas as it is now
            w._reset_step()
            status = "marked done - next step"
        elif key == "skip" and machine.status == "stuck":
            machine.skip()
            w.rebase(capture())
            w._reset_step()
            status = "skipped - next step"
        elif key == "outline":
            outline = not outline
    if machine.status == "complete":
        panel.update(step=steps[-1], index=len(steps), count=len(steps), outline=outline, watching=True,
                     lessons=[s.model_dump() for s in machine.steps], status="painting complete",
                     lesson=machine.steps[-1].model_dump())
        time.sleep(1.0)     # let the page pick up the final state
    print(f"session: {machine.status}, step {machine.state['current'] + 1} of {len(steps)}")


if __name__ == "__main__":
    main()
