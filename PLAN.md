# RobBoss — Build Plan

Three tracks, four validation spikes, one shared contract.

**In scope:** Sprout spatial I/O · Gemini plan + critique · GIMP base layers
**Deferred:** ElevenLabs voice · Backboard memory

---

## Hour zero — the shared contract

Two artifacts let three tracks proceed without talking to each other. Agree on
these, commit them, then split up.

### 1. Canvas space

All masks and coordinates are normalized `0–1` within the detected canvas quad,
origin top-left. Track 1 owns the *only* code that converts to camera or
projector pixels.

### 2. `step.json` — the only object that crosses track boundaries

```json
{
  "index": 1,
  "name": "Block in the sky",
  "mask_path": "layers/01_sky.png",
  "target_rgb": [94, 142, 183],
  "mix": [
    { "pigment": "phthalo blue",   "parts": 3 },
    { "pigment": "titanium white", "parts": 1 }
  ],
  "brush": "1in flat",
  "technique": "flat wash, horizontal",
  "stroke_dir_deg": 0,
  "success": "even coverage, no canvas showing through"
}
```

`mask_path` is the seam: Track 3 writes it, Track 1 reads it.

**Masks are files, not payloads.** Track 3 writes 8-bit PNGs to `layers/` at plan
time. The live loop reads from disk. Nothing calls GIMP after the plan exists —
MCP round-trips through a GUI app are too slow for the critique step.

---

## Gate 1 — validation spikes (first ~4 hours, concurrent)

Each spike answers one question that would invalidate a track's design.
Throwaway code, stated pass criterion, stated fallback. If a spike fails we take
the fallback rather than debug it into the night.

### Spike A — Lightguide registration · Track 1 · 3h

Can we project a shape onto a real canvas and have it land where we intend?
This is the spike the whole product rests on.

- **Method:** project a checkerboard, capture it, solve the camera↔projector
  homography. Then project a filled rectangle onto a canvas with physical tape
  marks and measure the offset at all four corners.
- **Pass:** ≤ 5 mm error at canvas corners, stable across 10 min.
- **Fallback:** consumer projector + webcam on a rig; if registration still
  drifts, drop to a tablet propped beside the canvas showing the same masks.

### Spike B — Reading paint under projected light · Track 1 · 1.5h

The projector contaminates every pixel the camera sees. Can we still judge the
painted color well enough to answer "is this color right?"

- **Method:** paint three known swatches. Capture each twice — projector live,
  and projector blanked to black for one frame — and compare both against a
  color-checker reading.
- **Pass:** ΔE < 10 on the blanked capture.
- **Fallback:** blank-before-capture becomes mandatory in the loop. If blanked
  capture still fails, color checking leaves the demo and critique covers
  coverage and placement only.

### Spike C — Posterize to clean masks · Track 3 · 3h incl. install

Does a value-quantized landscape actually decompose into paintable regions, or
into confetti?

- **Method:** headless GIMP via MCP on three landscapes — posterize to 5 values,
  separate by color, despeckle, export one normalized PNG mask per region.
- **Pass:** 5 masks, each ≥ 4% of canvas, no gaps between them.
- **Fallback:** GIMP is not installed and no MCP server is wired — that setup is
  inside this timebox. If it overruns, do the same operations in OpenCV
  (k-means + morphology) and keep the identical file contract.

### Spike D — Critique discrimination · Track 2 · 2.5h

Given a reference, a half-finished painting and a current step, does Gemini name
the one defect that matters — or list five vague ones?

- **Method:** hand-make six paintings with a single planted defect each (wrong
  value, missed region, wrong stroke direction, overblended, right but
  incomplete, correct). Score whether the planted defect is the returned
  adjustment.
- **Pass:** ≥ 4/6 planted defects named, **0 false alarms** on the correct one.
- **Fallback:** narrow the ask — give Gemini the current step's mask and ask only
  "is this region complete, and is its value too light or too dark?" A reliable
  narrow critique beats an unreliable open one.
- **Result (2026-10-03): PASS, with a narrowed scope.** Harness in
  `spikes/spike_d/`. Synthetic defects derived from a real reference painting,
  4 steps (sky, mountains, water, foreground) × 6 cases, 3 runs each,
  `gemini-3.8-flash`, structured output (`verdict`, `category`, `adjustment`).
  - **17/20** planted defects named by majority; **0/4** false alarms on the
    correct painting after a 3-run majority vote (1/12 single runs flagged one).
  - **Reliable:** wrong value (12/12 runs), missed region and incomplete
    (24/24), stroke direction on sky/mountains/water (9/9) — and the adjustment
    text points at the right place.
  - **Not reliable:** over-blending on already-smooth areas (mountains, water:
    ~1/6 runs), and stroke direction on dabbed foliage (my rotated-texture
    defect is probably not a real defect there).
  - **Decision:** the critique asks for **value + coverage** (plus stroke
    direction on flat-wash steps). Do not rely on blending/texture critique.
    Vote over 3 samples before showing a correction — a single sample can emit
    a spurious ADJUST. The "correct" case is only quiet when the step's region
    matches what is visibly paintable, so Track 3 masks must be accurate: the
    first two runs false-alarmed because *my test mask* left bare slivers.
  - **Caveats:** defects are generated by code, not photos of real paint under
    projector glare; one reference image; free-tier quota hit before billing.

---

## Gate 2 — the three tracks

One owner, one interface each. Tracks 2 and 3 are fully mockable and need no
hardware at any point, so a Track 1 hardware failure cannot stall them.

### Track 1 — Lightguide & spatial I/O  *(hardware critical path)*

Owns every conversion between the physical workspace and canvas space. The only
track that can't be faked.

Spikes owned: **A**, **B**

- **Canvas quad detection** — find the canvas in frame; re-detect on demand, not
  per frame.
- **Blank-and-capture** — projector to black, settle, grab frame, restore
  overlay. One function, used everywhere.
- **Overlay renderer** — region fill, boundary lines, stroke-direction arrows,
  correction flash.
- **Rectification** — warp capture to canvas space so Track 2 always sees a
  square canvas.

```
capture_canvas() -> ndarray
project_overlay(mask, style) -> None
```

### Track 2 — Plan & critique  *(no hardware needed)*

Turns an image into an ordered teaching sequence, then judges the painter
against it. Develops entirely against stock photos of partial paintings.

Spikes owned: **D**

- **Planner** — takes Track 3's masks and orders them into five steps with
  pigment ratios, brush and technique. Gemini orders and describes; it never
  invents coordinates.
- **Critique call** — reference + rectified capture + current step → `READY` or
  exactly one adjustment.
- **Schema enforcement** — structured output, retry on violation, so the
  renderer never receives a malformed step.
- **Step machine** — advance / retry / stuck-after-3-tries, held in a plain dict
  for now (Backboard slots in here later).
- **Watcher (hands-free)** — no button. A motion gate debounces on the painter's
  hand: checks run only after the canvas has been still ~1.5 s and changed. Local
  CV (`track2/cv.py`) measures coverage and value inside the step's mask — exact,
  free, and authoritative for those two. Gemini is called only to confirm a step
  that looks complete, and may only object about stroke direction. Corrections
  need two agreeing measurements and are not repeated until the canvas changes.
  Value corrections give a concrete fix ("mix in about 2 parts of titanium white to
  your 10-part mix", `track2/mixfix.py`) and a map of the off-value areas to project.
  A step still wrong after 3 corrections is re-planned once by Gemini (same masks,
  revised mix/technique) instead of just going stuck. A hand/brush guard skips
  captures with unexpected skin colour or change outside the step's region.
  Needs from Track 1: a cheap frame for motion (no projector flash) plus the
  existing flash-lit `capture_canvas()` for measurement. Thresholds in
  `WatchConfig` are rig-dependent and need tuning on the real camera.

```
plan(ref, masks) -> [step]
critique(ref, capture, step) -> verdict
```

### Track 3 — Base layers via GIMP  *(no hardware needed)*

Derives the actual pixels from the reference image so the model never has to
guess a polygon. Runs once at upload; its output is files on disk.

Spikes owned: **C**

- **GIMP MCP setup** — install, wire the server, confirm headless Script-Fu
  round-trips.
- **Value decomposition** — posterize to N values, separate, clean up specks and
  holes.
- **Per-region swatch** — average color of each region; feeds `target_rgb` so
  Track 2 doesn't eyeball it.
- **Composition lines** — edge detect and threshold into a thin projectable line
  layer.
- **Export** — normalized PNGs into `layers/` with a manifest. The file contract
  is the whole integration.

```
decompose(ref, n=5) -> layers/*.png + manifest.json
```

---

## Gate 3 — integration order

Sequenced, because each join needs the previous one working.

| Join | Tracks | What it proves |
|---|---|---|
| 1 | 3 → 1 | Project a real mask from `layers/` onto the canvas. Validates the file contract and the coordinate space in one shot. Do it the moment both spikes clear. |
| 2 | 3 → 2 | Planner consumes the real manifest instead of mocked masks. Pure software — can run in parallel with Join 1. |
| 3 | 1 → 2 | Blanked capture feeds the critique call. Closes the loop: plan → project → paint → capture → critique → correct → advance. |
| — | Rehearsal | Fixed reference, pre-mixed paints, locked lighting, and a deliberate planted mistake so the adaptive critique visibly fires. Rehearse the failure, not just the success. |

---

## Known risks

**Deferring voice turns "check my progress" into a button.** The painter puts
down a brush and clicks, which changes demo pacing: the projected correction has
to be obvious enough to carry the moment on its own. That makes Track 1's
correction flash a demo feature, not a polish item.

**Spikes A and D fail differently.** A fails loudly — you see the rectangle in
the wrong place. D fails quietly: Gemini returns confident, plausible, wrong
adjustments and you won't notice until the demo. Hence the planted-defect set
with known answers, and hence the clean painting in it — a critic that always
finds something is worse than no critic.

**GIMP is not installed on this machine** and no GIMP MCP server is configured.
That's the one new dependency that can't be mocked away, so it leads Track 3's
list and sits inside Spike C's timebox.
