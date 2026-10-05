package com.example.robboss_mobile_app.model

/**
 * Ports of track2/schema.py: MAX_TOTAL_PARTS, validate_draft, and verdict_is_consistent,
 * plus the critique vote in track2/critique.py.
 */
object Rules {
    const val MAX_TOTAL_PARTS = 12

    fun validateDraft(draft: PlanDraft, maskIds: List<String>, keepOrder: Boolean = false): List<String> {
        val errors = mutableListOf<String>()
        val got = draft.steps.map { it.maskId }
        if (got.sorted() != maskIds.sorted()) {
            errors += "steps must use each of $maskIds exactly once, got $got"
        } else if (keepOrder && got != maskIds) {
            errors += "steps must stay in this exact order (later layers paint over earlier ones): $maskIds, got $got"
        }
        for (step in draft.steps) {
            val tag = step.maskId
            if (step.mix.isEmpty()) errors += "$tag: mix is empty"
            var total = 0
            for (part in step.mix) {
                if (part.pigment !in Palette.pigments) {
                    errors += "$tag: pigment '${part.pigment}' not in palette ${Palette.pigments}"
                }
                if (part.parts !in 1..MAX_TOTAL_PARTS) {
                    errors += "$tag: parts must be 1..$MAX_TOTAL_PARTS, got ${part.parts}"
                }
                total += part.parts
            }
            if (total > MAX_TOTAL_PARTS) {
                errors += "$tag: total parts $total exceeds $MAX_TOTAL_PARTS"
            }
            if (step.brush !in Palette.brushes) {
                errors += "$tag: brush '${step.brush}' not in ${Palette.brushes}"
            }
            if (step.strokeDirDeg !in 0..359) {
                errors += "$tag: stroke_dir_deg must be 0..359, got ${step.strokeDirDeg}"
            }
            if (step.name.isBlank()) errors += "$tag: name is empty"
            if (step.technique.isBlank()) errors += "$tag: technique is empty"
            if (step.success.isBlank()) errors += "$tag: success is empty"
        }
        return errors
    }

    fun verdictIsConsistent(verdict: Verdict): Boolean {
        if (verdict.verdict == "READY") {
            return verdict.category == "none" && verdict.adjustment.isBlank()
        }
        return verdict.category != "none" && verdict.adjustment.isNotBlank()
    }

    /** READY unless a majority of samples say ADJUST; then the most common category wins. */
    fun vote(samples: List<Verdict>): Verdict {
        if (samples.isEmpty()) error("no valid samples to vote over")
        val adjusts = samples.filter { it.verdict == "ADJUST" }
        if (adjusts.size * 2 <= samples.size) {
            return Verdict(verdict = "READY", category = "none", adjustment = "")
        }
        val top = adjusts.groupingBy { it.category }.eachCount().maxBy { it.value }.key
        return adjusts.first { it.category == top }
    }
}
