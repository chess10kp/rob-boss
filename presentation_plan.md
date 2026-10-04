Rough 2 minute demo plan (Bob Ross sunset lesson — see `config/rehearsal.json`):

## Setup

- Align paper and supplies; lock lighting if rehearsed (`uv run python -m rehearsal check`).
- Start: `uv run python -m track1.show` — upload reference, calibrate bare canvas when prompted.
- Pre-mix paints from `uv run python -m rehearsal sheet` if doing a full rig run.

## Flow

1. **Step 1** — First big wash: projected mask, mix/brush/technique on screen. Paint, hands off ~2.5 s → system advances when coverage/value look good (shows the normal loop).
2. **Step 2** — Same loop briefly, then **plant the mistake** (do not advance past it).

## Planted mistake (primary)

On **step 2**, paint **too dark**: use the step’s mix **plus ~2 extra parts burnt umber** (`rehearsal.json`). Expected: **value** ADJUST — wrong areas blink on the paper, control page says what to fix. Correct it → step advances on its own.

**Backup mistake:** leave obvious **bare / patchy** spots in the step region → **coverage** ADJUST (also CV-driven, very visible).

Avoid relying on stroke-direction critique live (Gemini-only, flakier on stage).

## Optional if time

- Mention pre-mixed cups labelled 1…N; don’t run the full ~10-step lesson in two minutes.
- Software-only sanity check before the rig: `uv run python -m rehearsal dry-run`.
