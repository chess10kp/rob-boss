package com.example.robboss_mobile_app.domain

import com.example.robboss_mobile_app.model.Palette
import com.example.robboss_mobile_app.model.Rules

/** Prompt text ported from track2/planner.py (ORDER_FREE: the remote GIMP job is not a layer stack). */
object PlannerPrompts {
    fun lesson(n: Int, facts: String, feedback: String): String {
        val order = "Return ALL $n regions in the best painting order for a beginner (steps list order is\n" +
            "the teaching order; use each mask_id exactly once)."
        return """You are an oil/acrylic painting teacher turning a reference image into a
step-by-step lesson for a beginner. The reference is image 1. It has been split into
$n regions (masks); each mask follows, with its measured facts.

$facts

$order
""" + fields() + "\n" + feedback
    }

    private fun fields(): String = """For each region give:
- name: short, friendly title, e.g. "Let's put in a happy little sky"
- mix: pigments with integer parts that mix to roughly the region's mean color.
  Pigments must come from: ${Palette.pigments.joinToString(", ")}. Total parts at most ${Rules.MAX_TOTAL_PARTS}.
- brush: one of ${Palette.brushes.joinToString(", ")}
- technique: one or two sentences on how to apply it (stroke style, blending, edges)
- stroke_dir_deg: dominant stroke direction, 0 = horizontal, 90 = vertical, 0..359
- success: one sentence a camera could check (coverage, value, stroke direction)

Never invent coordinates or shapes; refer to regions only by what they look like.

""" + VOICE
}
