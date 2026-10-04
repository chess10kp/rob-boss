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

**Steps are an ordered stack, not a partition.** Spike C2 replaced the disjoint
value partition with a back-to-front layer stack, and the contract follows:

- Masks **overlap**, and their coverages sum past 100%. Step *k* paints over
  everything behind it, including area a later step will bury.
- Each step gains two optional fields — `opacity` and `edge_softness_px` — so a
  thin ground wash and a thick final accent are distinguishable.
- **`mix` is produced, not authored, and it is a hard constraint.** Every step
  is limited to at most three mixes drawn from the thirteen-tube palette, mixed
  under single-constant Kubelka-Munk (`track3/palette.py`). A step also carries
  `mixes` (every mix it needs, with coverage) and `mix_description`, the sayable
  form — past a ratio nobody could measure, "384 parts white to 1 part phthalo"
  becomes "titanium white with a speck of phthalo blue".
- **Opacity is solved, not assumed.** Each pass solves for the pigment that
  *lands* on its target given what is already wet beneath it, and thickens
  itself when a thin pass could not reach — a dark over a light needs body.
  This is what makes the stack sequential: step *k*'s mix depends on steps
  1..*k*−1.
- `stroke_dir_deg` is derived, not authored: it is the principal axis of the
  step's visible region.
- The validator invariant is **reconstruction under a palette**, not
  disjointness and not reconstruction alone. `gap_pixels`/`overlap_pixels` are
  retired. Reconstruction on its own is retired as a *target*: the ΔE-optimal
  layer is a masked copy of the photograph, which composites to a near-perfect
  score while being a reveal rather than a painting — the metric rewarded
  exactly the failure we were trying to catch. See Spike C2's second result.
- Four metrics replace the single ΔE gate: **mixes per step** (≤ 3), **per-step
  improvement** in whole-canvas mean ΔE (a reveal only ever improves its own
  region), **role re-entries** (how often the order returns to a material it had
  finished), and **detail retained** against the reference. A painting carries
  less high-frequency content than a photograph and gains it late — but it has
  to gain it, and nothing in the old gate noticed when it never did.
- **The ≥4%-per-mask floor is retired with them.** It was an artifact of the
  partition: under a layer stack a 1% highlight pass is correct, because final
  accents are supposed to be small. Spike C's "lightest mask too small" failures
  dissolve under this model. What survives is the paintability metric —
  component count per step — which carries over unchanged.
- Step count rises from 5 to **10–11**. Track 2's planner either merges stages
  into coarser teaching steps or the session gets longer. That is a product
  decision, not a technical one, and it is open.

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

**Result (2026-10-04): FAIL the stated contract; mixed on paintability.**
`maorcc/gimp-mcp` at commit `09bfb2d` successfully drove GIMP 3.2.6 under
Xvfb. All three exported partitions had zero gaps and zero overlaps after
despeckling, and two of three scenes produced clean regions. Fixed five-level
posterization did not allocate enough area to the extreme values: Bob Ross
lightest 0.21%; mountain darkest 0.27% and lightest 1.61%; forest darkest 1.15%
and lightest 3.18%. The foliage-heavy forest also remained fragmented (17
mid-value and 21 light-value components). Artifacts and metrics are in
`artifacts/spike-c/`. Conclusion: fixed GIMP posterization is not the production
decomposition; use adaptive luminance clustering while preserving this file
contract and validator.

### Spike C2 — Wet-on-wet layer stack · Track 3 · 2h

Spike C answered its question and in doing so invalidated it. A disjoint value
partition can only express "fill these N regions". It cannot express what a
landscape painter actually does: lay the whole sky down, then put the mountain
on top of it. The follow-up question is therefore not "how do we get cleaner
value bands" but "does an ordered, overlapping layer stack reconstruct a
landscape, and does its order read as a painting sequence".

- **Method:** `track3/layers.py` — pure numpy/PIL, no GIMP. Cluster each side of
  the waterline separately, score each region for depth by aerial perspective,
  order back to front, extend each layer behind its occluders, feather its
  silhouette by depth, then paint the stack in order — each pass solving for its
  pigment against the wet canvas and reducing to palette mixes
  (`track3/palette.py`).
- **Pass:** the stack reads as a painting sequence — every stage paintable from
  at most three palette mixes, every stage moving the whole canvas toward the
  reference, and the depth order not doubling back on itself.

**Result (2026-10-03): PASS on painted landscapes, FAIL on dense forest.**
**Revised (2026-10-04): the first result was measuring the wrong thing.**

The original pass criterion was mean ΔE < 8, and the stack met it on three of
four scenes. It met it by cheating. With each layer free to carry per-pixel
colour at full opacity, the ΔE-optimal layer is a masked copy of the reference,
and that is what the decomposition converged on: compositing the stack did not
paint the scene, it **uncovered** it one region at a time. Nothing blended,
nothing mixed optically, no step depended on any step before it, and the `mix`
field the contract had promised since hour zero was never populated by anything.
The metric could not see this, because copying the photograph is the way to
minimise it.

Two changes fix it, and both make the number worse on purpose:

1. **Palette constraint.** Each layer is reduced to at most three mixes from the
   thirteen tubes, blended into each other across the layer rather than
   hard-assigned. Thirteen tubes cannot hit a photograph's per-pixel colour, and
   should not be able to.
2. **Solve against the wet canvas.** Passes go down at 0.55–0.9 opacity and each
   solves for the pigment that lands on target *given what is already there*,
   thickening itself where a thin pass could not reach. Step *k* now genuinely
   depends on steps 1..*k*−1.

| Scene | Stages | mean ΔE | p95 | detail kept | max mixes/step | pigments | role re-entries |
|---|---:|---:|---:|---:|---:|---:|---:|
| `bobross.jpg` | 10 | 14.71 | 29.4 | 22% | 3 | 8 | 1 |
| bobross-sunset | 10 | 13.52 | 25.2 | 20% | 3 | 7 | 2 |
| mountain-lake | 11 | 8.11 | 15.9 | 20% | 3 | 11 | 2 |
| forest-lake | 16 | 12.31 | 27.6 | 16% | 3 | 12 | **9** |

**ΔE roughly doubled and that is the correct direction.** It is now the cost of
painting with real pigment instead of the score for copying, and the old < 8
gate is retired with the behaviour that produced it. Bare canvas stays 0.00%
everywhere, and no step on any scene makes the canvas worse — the whole picture
walks toward the reference pass by pass, which is what a painting does and what
a reveal cannot.

**Two blurs were throwing away the scene, and only one of them was obvious.**
The first reconstruction under the palette was visibly soft, and `detail` — the
metric added to catch exactly this — put a number on it: 0.18 against the
reference's 1.98, 9% retained, and *flat from step 7 to the end*, so the
accent passes contributed no detail at all. Two separate box blurs were
responsible, each able to flatten the scene on its own:

1. `extend_layers` built every layer's target as a radius-10 blur of the
   reference. That predates the palette work and was invisible before it.
   Reduced to radius 2 — enough for canvas weave and grain, not for edges.
2. A per-role box blur over the quantized field, added alongside the palette
   limit to stop mixes meeting at a hard line. Removing the first blur alone
   moved detail only 0.18 → 0.20, because this one was doing the same damage.
   It is gone: weighting the mixes by colour distance already blends them where
   the target varies smoothly, and leaves the jumps alone where it does not.

Together: detail 0.18 → 0.48 and mean ΔE 16.25 → 14.71 on `bobross`. Both blurs
were costing accuracy as well as detail, so nothing was traded for this.

Detail retained settles around 20%. That is the palette limit itself and it is
meant to be there — three mixes per layer cannot carry brush texture, and should
not. What matters is that silhouettes are now crisp: ridgelines, tree edges and
the sun read as shapes rather than as soft blobs.

The three painted/photographed landscapes decompose into **10–11 stages** whose
order reads correctly: toned ground, sky in bands, distant ridges, far treeline,
water, foreground mass, then a ~1% highlight pass — each naming a real mix, down
to "titanium white with a speck of phthalo blue" for the upper sky.

`forest-lake` still fails, for the same underlying reason, and **role re-entries
is now the number that says so**: 9, against 1–2 everywhere else. A swamp has no
depth planes — trunks occupy every depth at once — so the ordering keeps coming
back to foreground and water it had already finished. This is a scene-class
limit, not a tuning problem. Dense unstructured foliage is out of scope for a
decomposition built on aerial perspective, and the product should say so rather
than paper over it.

Known artifact, unchanged by this work: the grassfire amodal fill leaves faint
diamond outlines where a layer is extended far behind an occluder, visible along
mountain-lake's ridgeline.

Artifacts and per-stage metrics in `artifacts/spike-c2/`.

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
- **Layer decomposition** — cluster each side of the waterline, order back to
  front by aerial perspective, extend each layer behind its occluders, feather
  by depth. Supersedes posterize-and-separate (Spike C2).
- **Per-region swatch** — average color of each region; feeds `target_rgb` so
  Track 2 doesn't eyeball it.
- **Composition lines** — edge detect and threshold into a thin projectable line
  layer.
- **Export** — normalized PNGs into `layers/` with a manifest. The file contract
  is the whole integration.

```
decompose(ref) -> layers/*.png (RGBA) + masks/*.png + report.json
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

**Depth order is heuristic, and it is the thing that breaks first.** Aerial
perspective is a real cue but a soft one. It holds on scenes with separable
depth planes and collapses on dense foliage, which is exactly the `forest-lake`
failure. If reference images are user-supplied rather than curated, this needs
either a monocular depth model (Depth Anything V2 small, CPU, ~2.5GB of torch)
or a guard that rejects scenes whose depth ordering thrashes. Curating the demo
references is the cheap answer and probably the right one for the timebox.

**GIMP MCP requires a virtual display and a trusted local runtime.** GIMP 3.2.6
and the pinned MCP server are installed, but pure `--no-interface` mode cannot
construct images on this machine. Run it under Xvfb, keep the unauthenticated
localhost port 9877 short-lived, and stop the server after batch processing.
