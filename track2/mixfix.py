"""Turn a measured lightness error into a concrete mix fix ("add about 1 part white").

Model: mixing paints averages lightness (L*) in proportion to the parts. If the painter's
current mix has T parts at lightness L_c and we add p parts of a pigment at L_p, the result is
(T*L_c + p*L_p) / (T + p). Solve that for the reference lightness L_r. It is a rough model
(real paint is not linear and drying shifts value), so the answer is rounded to half parts and
capped, and always phrased as "about".
"""
from __future__ import annotations

from track2.palette import PIGMENTS
from track2.schema import Step

# Approximate L* of each pigment mixed down to a typical tint; edit with the palette.
PIGMENT_L = {
    "titanium white": 96.0,
    "ivory black": 12.0,
    "burnt umber": 28.0,
    "ultramarine blue": 30.0,
    "cadmium red": 50.0,
    "cadmium yellow": 82.0,
}
WHITE = "titanium white"


def parts_to_add(total_parts: float, l_canvas: float, l_ref: float, l_pigment: float) -> float:
    """Parts of a pigment at `l_pigment` that move the mix from l_canvas to l_ref (capped at T)."""
    gap = l_pigment - l_ref
    if abs(gap) < 5.0 or (l_ref - l_canvas) * gap < 0:   # pigment can't get there from here
        return float(total_parts)
    return min(float(total_parts), max(0.0, total_parts * (l_ref - l_canvas) / gap))


def fmt_parts(p: float) -> str:
    halves = max(1, round(p * 2))                       # half-part resolution, at least a half
    if halves == 1:
        return "half a part"
    n = halves / 2
    text = f"{int(n)}" if halves % 2 == 0 else f"{n:.1f}"
    return f"{text} part{'s' if n > 1 else ''}"


def darkest_in_mix(step: Step) -> str:
    known = [m.pigment for m in step.mix if m.pigment in PIGMENT_L]
    pool = known or [p for p in PIGMENTS if p in PIGMENT_L]
    return min(pool, key=lambda p: PIGMENT_L[p]) if pool else "ivory black"


def advise(step: Step, l_canvas: float, l_ref: float) -> str:
    total = sum(m.parts for m in step.mix)
    if l_canvas < l_ref:
        pigment = WHITE
        word = "dark"
    else:
        pigment = darkest_in_mix(step)
        word = "light"
    p = parts_to_add(total, l_canvas, l_ref, PIGMENT_L.get(pigment, 50.0))
    return f"The paint is too {word}. Mix in about {fmt_parts(p)} of {pigment} to your {total}-part mix."
