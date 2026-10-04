"""Spike D: does Gemini name the one planted defect, and stay quiet on the correct painting?

Usage: python run_spike.py [--model gemini-3.8-flash] [--runs 3]
Needs GEMINI_API_KEY in the environment or a .env file.
"""
import argparse
import json
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from dotenv import load_dotenv
from google import genai
from google.genai import errors as genai_errors
from google.genai import types
from PIL import Image

HERE = Path(__file__).parent
load_dotenv(HERE.parent.parent / ".env")
load_dotenv(HERE / ".env")

CATEGORIES = ["value", "coverage", "stroke_direction", "blending", "none"]
SCHEMA = {
    "type": "object",
    "properties": {
        "verdict": {"type": "string", "enum": ["READY", "ADJUST"]},
        "category": {"type": "string", "enum": CATEGORIES},
        "adjustment": {"type": "string", "description": "One imperative sentence; empty if READY"},
    },
    "required": ["verdict", "category", "adjustment"],
}

PROMPT = """You are a painting coach checking a student's work on ONE step of a landscape.
Image 1 is the reference the student is copying. Image 2 is a photo of their canvas
right now (bare canvas is off-white). Judge ONLY the current step, nothing else.

Current step:
{step}

If the step is done well enough to move on, return verdict READY, category none, empty adjustment.
Otherwise return verdict ADJUST with exactly ONE adjustment: the single most important
defect, as one imperative sentence. Never list several problems. Do not invent problems
on a step that is done. Category meanings: value = paint too light/dark vs target,
coverage = areas of the step's region unpainted or incomplete, stroke_direction = strokes
not running the way the step says, blending = texture/edges lost or smeared."""


def critique(client, model, ref, capture, step):
    for attempt in range(6):
        try:
            resp = client.models.generate_content(
                model=model,
                contents=[PROMPT.format(step=json.dumps(step, indent=2)), ref, capture],
                config=types.GenerateContentConfig(
                    response_mime_type="application/json", response_schema=SCHEMA, temperature=0.2
                ),
            )
            return json.loads(resp.text)
        except genai_errors.ServerError:
            if attempt == 5:
                raise
            time.sleep(5 * 2 ** attempt)
        except genai_errors.ClientError as e:
            # Per-minute free-tier limit: wait and retry. A daily cap (long retryDelay) is fatal.
            if e.code != 429 or attempt == 5 or "PerDay" in str(e):
                raise
            time.sleep(15)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default="gemini-3.8-flash")
    ap.add_argument("--runs", type=int, default=3)
    ap.add_argument("--steps", default="", help="comma-separated step indexes, default all")
    args = ap.parse_args()
    if not os.environ.get("GEMINI_API_KEY"):
        sys.exit("Set GEMINI_API_KEY (env var or .env file).")

    truth = json.loads((HERE / "cases" / "truth.json").read_text())
    wanted = {int(x) for x in args.steps.split(",") if x}
    client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])
    ref = Image.open(HERE / "cases" / "reference.png").convert("RGB")
    (HERE / "out").mkdir(exist_ok=True)
    out_path = HERE / "out" / f"results_{args.model}.json"

    jobs = []  # (step_idx, cid, run)
    for st in truth["steps"]:
        if wanted and st["step"]["index"] not in wanted:
            continue
        for cid in st["cases"]:
            jobs += [(st["step"]["index"], cid, r) for r in range(args.runs)]
    by_idx = {st["step"]["index"]: st for st in truth["steps"]}

    def work(job):
        idx, cid, _ = job
        st = by_idx[idx]
        img = Image.open(HERE / "cases" / st["dir"] / f"{cid}.png").convert("RGB")
        return job, critique(client, args.model, ref, img, st["step"])

    results = {}
    with ThreadPoolExecutor(max_workers=6) as pool:
        for (idx, cid, _), r in pool.map(work, jobs):
            exp = by_idx[idx]["cases"][cid]["expected_category"]
            slot = results.setdefault(str(idx), {}).setdefault(cid, {"expected": exp, "runs": [], "hits": []})
            slot["runs"].append(r)
            slot["hits"].append(r["category"] == exp)
    out_path.write_text(json.dumps(results, indent=2))

    total_named = total_planted = false_alarms = correct_runs = 0
    for idx, cases in sorted(results.items()):
        print(f"\n=== step {idx}: {by_idx[int(idx)]['step']['name']} ===")
        named = []
        for cid, c in cases.items():
            print(f"[{cid}] expected={c['expected']} hits={sum(c['hits'])}/{args.runs}")
            for r in c["runs"]:
                print(f"   {r['verdict']:6} {r['category']:16} {r['adjustment']}")
            if cid == "correct":
                false_alarms += sum(not h for h in c["hits"])
                correct_runs += args.runs
            else:
                total_planted += 1
                if sum(c["hits"]) > args.runs / 2:
                    named.append(cid)
                    total_named += 1
        print(f"  named (majority): {len(named)}/{len(cases) - 1}  missed: "
              f"{[c for c in cases if c != 'correct' and c not in named]}")
    print("\n=== Spike D summary ===")
    print(f"model: {args.model}   runs/case: {args.runs}   steps: {sorted(results)}")
    print(f"planted defects named (majority): {total_named}/{total_planted}")
    print(f"false alarms on correct paintings: {false_alarms}/{correct_runs} runs")
    voted_fa = sum(sum(c["correct"]["hits"]) <= args.runs / 2 for c in results.values() if "correct" in c)
    print(f"false alarms after {args.runs}-run majority vote: {voted_fa}/{len(results)} steps")
    print("PASS" if total_named >= total_planted * 4 / 5 and false_alarms == 0 else "FAIL")


if __name__ == "__main__":
    main()
