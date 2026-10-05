package com.example.robboss_mobile_app.domain

import com.example.robboss_mobile_app.data.GeminiCompleter
import com.example.robboss_mobile_app.data.GeminiPart
import com.example.robboss_mobile_app.data.stripFence
import com.example.robboss_mobile_app.model.AppJson
import com.example.robboss_mobile_app.model.Rules
import com.example.robboss_mobile_app.model.Step
import com.example.robboss_mobile_app.model.Verdict
import com.example.robboss_mobile_app.model.jsonString
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.async
import kotlinx.coroutines.awaitAll
import kotlinx.coroutines.coroutineScope

private const val MASK_NOTE =
    " Image 3 is this step's region: WHITE is where paint belongs now, black is not this" +
        " step's business (ignore it, whether painted or bare). Judge coverage and value" +
        " only inside the white area."

/** Three Gemini samples, then the same majority vote as track2/critique.py. */
class Critic(private val gemini: GeminiCompleter) {
    suspend fun critique(refJpeg: ByteArray, captureJpeg: ByteArray, maskPng: ByteArray, step: Step, samples: Int = 3): Verdict {
        val parts = listOf(
            GeminiPart.Text(prompt(step)),
            GeminiPart.Jpeg(refJpeg),
            GeminiPart.Jpeg(captureJpeg),
            GeminiPart.Png(maskPng),
        )
        val replies = coroutineScope {
            (0 until samples).map {
                async(Dispatchers.IO) { consistentSample(parts) }
            }.awaitAll().filterNotNull()
        }
        if (replies.isEmpty()) error("The critique didn't come back clearly. Try the photo again.")
        return Rules.vote(replies)
    }

    private fun consistentSample(parts: List<GeminiPart>): Verdict? {
        repeat(3) {
            val verdict = try {
                val text = stripFence(gemini.complete(parts, verdictSchema()))
                AppJson.decodeFromString(Verdict.serializer(), text)
            } catch (_: Exception) {
                return@repeat
            }
            if (Rules.verdictIsConsistent(verdict)) return verdict
        }
        return null
    }

    private fun prompt(step: Step): String = """You are a painting coach checking a student's work on ONE step of a landscape.
Image 1 is the reference the student is copying. Image 2 is a photo of their canvas right
now (bare canvas is off-white).$MASK_NOTE Judge ONLY the current step, nothing else. Earlier
steps are already painted; later steps' areas are intentionally still bare.

Current step:
${coachJson(step)}

If the step is done well enough to move on, return verdict READY, category none, empty
adjustment. Otherwise return verdict ADJUST with exactly ONE adjustment: the single most
important defect, as one gentle sentence that says what to do next. Never list several
problems. Do not invent problems on a step that is done. Categories: value = paint too
light/dark vs the target, coverage = parts of this step's region unpainted or incomplete,
stroke_direction = strokes not running the way the step says.

""" + VOICE
}

internal fun coachJson(step: Step): String {
    val mix = step.mix.joinToString(",\n") { part ->
        "    {\n      \"pigment\": ${jsonString(part.pigment)},\n      \"parts\": ${part.parts}\n    }"
    }
    val rgb = step.targetRgb.joinToString(", ")
    return """{
  "index": ${step.index},
  "name": ${jsonString(step.name)},
  "target_rgb": [$rgb],
  "mix": [
$mix
  ],
  "brush": ${jsonString(step.brush)},
  "technique": ${jsonString(step.technique)},
  "stroke_dir_deg": ${step.strokeDirDeg},
  "success": ${jsonString(step.success)}
}"""
}
