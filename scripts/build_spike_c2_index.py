"""Build artifacts/spike-c2/index.html from the per-scene report.json files.

One page, not sixteen tabs: the summary table, then per scene the reference
against the reconstruction, the step sequence, the individual layers, the error
map, and the step table with the mix each pass calls for.
"""

from __future__ import annotations

import html
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ARTIFACTS = ROOT / "artifacts" / "spike-c2"

# Scene order puts the one that fails last.
SCENES = [
    ("bobross", "../../bobross.jpg"),
    ("bobross-sunset", "../../fixtures/spike_c/bobross-sunset.jpg"),
    ("mountain-lake", "../../fixtures/spike_c/mountain-lake.jpg"),
    ("forest-lake", "../../fixtures/spike_c/forest-lake.jpg"),
]

STYLE = """
:root{color-scheme:dark}
body{background:#16181c;color:#e8e8ea;font:14px/1.5 system-ui,sans-serif;margin:0;padding:32px 40px}
h1{font-size:20px;margin:0 0 4px}
h2{font-size:17px;margin:40px 0 2px;border-top:1px solid #2e3238;padding-top:22px}
.sub{color:#9aa0a8;margin:0 0 18px}
table{border-collapse:collapse;margin:14px 0 8px;font-variant-numeric:tabular-nums}
th,td{padding:4px 14px 4px 0;text-align:left;vertical-align:top}
th{color:#9aa0a8;font-weight:500}
td.n{text-align:right}
td.good{color:#7fd18c}td.bad{color:#f08a7a}
.pair{display:flex;gap:12px;flex-wrap:wrap;margin:10px 0}
.pair figure{margin:0;flex:1 1 380px}
figcaption{color:#9aa0a8;font-size:12px;margin:5px 0 0}
img{max-width:100%;display:block;border-radius:4px;background:#000}
.swatch{display:inline-block;width:11px;height:11px;border-radius:2px;margin-right:6px;
  vertical-align:-1px;border:1px solid #0006}
a{color:#7fb2ff}
.note{color:#9aa0a8;max-width:62em;margin:8px 0 0}
"""


def swatch(rgb: list[int]) -> str:
    return f"<span class=swatch style='background:rgb({rgb[0]},{rgb[1]},{rgb[2]})'></span>"


def step_table(report: dict) -> str:
    rows = [
        "<table><tr><th>#<th>stage<th>role<th>op<th>mixes<th>canvas ΔE"
        "<th>Δ<th>detail<th>pigment mix</tr>"
    ]
    for step in report["steps"]:
        improvement = step["improvement_delta_e"]
        css = "good" if improvement >= 0 else "bad"
        mixes = " ".join(
            swatch(entry["rgb"]) + html.escape(entry["description"])
            for entry in step["mixes"]
        )
        rows.append(
            f"<tr><td class=n>{step['index']}</td>"
            f"<td>{html.escape(step['name'])}</td>"
            f"<td>{step['role']}</td>"
            f"<td class=n>{step['opacity']:.2f}</td>"
            f"<td class=n>{step['mix_count']}</td>"
            f"<td class=n>{step['canvas_delta_e']:.2f}</td>"
            f"<td class='n {css}'>{improvement:+.2f}</td>"
            f"<td class=n>{step['detail']:.2f}</td>"
            f"<td>{mixes}</td></tr>"
        )
    rows.append("</table>")
    return "".join(rows)


def main() -> int:
    reports = {
        name: json.loads((ARTIFACTS / name / "report.json").read_text())
        for name, _ in SCENES
    }

    out = [
        "<!doctype html><meta charset=utf-8>",
        "<title>Spike C2 — palette-constrained wet-on-wet stack</title>",
        f"<style>{STYLE}</style>",
        "<h1>Spike C2 — palette-constrained wet-on-wet layer stack</h1>",
        "<p class=sub>Back-to-front overlapping layers, each limited to three mixes "
        "from the thirteen-tube palette and solved against what is already wet.</p>",
        "<p class=note>Reconstruction ΔE is a cost here, not a target. The ΔE-optimal "
        "layer is a masked copy of the photograph, which composites perfectly and is a "
        "reveal rather than a painting; the numbers that separate the two are "
        "<b>mixes per step</b> (how few colours a pass needs) and <b>Δ</b> (how much "
        "each pass moves the <i>whole</i> canvas toward the reference — a reveal only "
        "ever improves its own region). <b>Role re-entries</b> counts how often the "
        "order comes back to a material it had already finished, which is what a scene "
        "with no depth planes looks like from the inside. <b>Detail</b> is "
        "high-frequency content (mean distance of lightness from its local mean); a "
        "painting carries less of it than a photograph and gains it late, but it has "
        "to gain it.</p>",
        "<table><tr><th>scene<th>stages<th>mean ΔE<th>p95<th>detail kept"
        "<th>max mixes/step<th>pigments<th>role re-entries<th>bare canvas</tr>",
    ]
    for name, _ in SCENES:
        report = reports[name]
        reconstruction = report["reconstruction"]
        paintability = report["paintability"]
        out.append(
            f"<tr><td><a href='#{name}'>{name}</a></td>"
            f"<td class=n>{report['stage_count']}</td>"
            f"<td class=n>{reconstruction['mean_delta_e']:.2f}</td>"
            f"<td class=n>{reconstruction['p95_delta_e']:.2f}</td>"
            f"<td class=n>{report['detail']['retained'] * 100:.0f}%</td>"
            f"<td class=n>{paintability['max_mixes_per_step']}</td>"
            f"<td class=n>{paintability['pigment_count']}</td>"
            f"<td class='n {'good' if paintability['depth_order_stable'] else 'bad'}'>"
            f"{len(paintability['role_reentries'])}</td>"
            f"<td class=n>{reconstruction['bare_canvas_fraction'] * 100:.2f}%</td></tr>"
        )
    out.append("</table>")

    for name, reference in SCENES:
        report = reports[name]
        reconstruction = report["reconstruction"]
        order = " &rsaquo; ".join(
            f"{step['index']}. {html.escape(step['name'])}" for step in report["steps"]
        )
        out += [
            f"<h2 id='{name}'>{name} — {report['stage_count']} stages, "
            f"mean ΔE {reconstruction['mean_delta_e']:.2f}</h2>",
            f"<p class=sub>{order}</p>",
            "<div class=pair>",
            f"<figure><img src='{reference}'><figcaption>reference</figcaption></figure>",
            f"<figure><img src='{name}/reconstruction.png'>"
            "<figcaption>reconstruction (the stack, painted in order)</figcaption></figure>",
            "</div>",
            step_table(report),
            f"<div><img src='{name}/steps-contact-sheet.png'>"
            "<figcaption>cumulative canvas after each stage</figcaption></div>",
            f"<div><img src='{name}/layers-contact-sheet.png'>"
            "<figcaption>each layer alone (RGBA, feathered)</figcaption></div>",
            f"<div><img src='{name}/error-map.png'>"
            "<figcaption>reconstruction error &times;6 — bright = where it misses"
            "</figcaption></div>",
        ]

    (ARTIFACTS / "index.html").write_text("\n".join(out))
    print(f"wrote {ARTIFACTS / 'index.html'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
