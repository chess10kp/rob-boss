package com.example.robboss_mobile_app.domain

import com.example.robboss_mobile_app.data.GeminiCompleter
import com.example.robboss_mobile_app.data.GeminiPart
import com.example.robboss_mobile_app.data.stripFence
import com.example.robboss_mobile_app.model.AppJson
import com.example.robboss_mobile_app.model.MaskFile
import com.example.robboss_mobile_app.model.MixPart
import com.example.robboss_mobile_app.model.PlanDraft
import com.example.robboss_mobile_app.model.Rules
import com.example.robboss_mobile_app.model.Step

/** Orders GIMP masks into a teaching sequence. Retries when the draft fails validation. */
class Planner(private val gemini: GeminiCompleter) {
    fun plan(refJpeg: ByteArray, masks: List<MaskFile>): List<Step> {
        val ordered = masks.sortedBy { it.relativePath }
        val ids = ordered.map { it.facts.maskId }
        val facts = ordered.joinToString("\n") { it.facts.toJsonLine() }
        var feedback = ""
        var errors = listOf("no reply")
        repeat(MAX_TRIES) {
            val parts = buildList {
                add(GeminiPart.Text(PlannerPrompts.lesson(ordered.size, facts, feedback)))
                add(GeminiPart.Jpeg(refJpeg))
                ordered.forEach { mask ->
                    add(GeminiPart.Text("mask_id=${mask.facts.maskId}"))
                    add(GeminiPart.Png(mask.png))
                }
            }
            val draft = try {
                AppJson.decodeFromString(PlanDraft.serializer(), stripFence(gemini.complete(parts, planSchema())))
            } catch (error: Exception) {
                errors = listOf("reply was not valid JSON (${error.message})")
                feedback = feedbackFor(errors)
                return@repeat
            }
            errors = Rules.validateDraft(draft, ids, keepOrder = false)
            if (errors.isEmpty()) return toSteps(draft, ordered)
            feedback = feedbackFor(errors)
        }
        error("planner could not produce a valid plan after $MAX_TRIES tries: $errors")
    }

    private fun feedbackFor(errors: List<String>): String =
        "Your previous answer was invalid, fix these and answer again:\n- " + errors.joinToString("\n- ")

    private fun toSteps(draft: PlanDraft, masks: List<MaskFile>): List<Step> {
        val byId = masks.associateBy { it.facts.maskId }
        return draft.steps.mapIndexed { index, draftStep ->
            val mask = byId.getValue(draftStep.maskId)
            Step(
                index = index + 1,
                name = draftStep.name,
                maskPath = mask.relativePath,
                maskId = draftStep.maskId,
                targetRgb = mask.facts.meanRgb,
                mix = draftStep.mix.map { MixPart(it.pigment, it.parts) },
                brush = draftStep.brush,
                technique = draftStep.technique,
                strokeDirDeg = draftStep.strokeDirDeg,
                success = draftStep.success,
            )
        }
    }

    private companion object {
        const val MAX_TRIES = 3
    }
}
