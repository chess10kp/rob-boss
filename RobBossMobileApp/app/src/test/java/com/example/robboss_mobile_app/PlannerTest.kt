package com.example.robboss_mobile_app

import com.example.robboss_mobile_app.data.GeminiCompleter
import com.example.robboss_mobile_app.data.GeminiPart
import com.example.robboss_mobile_app.domain.Planner
import com.example.robboss_mobile_app.domain.PlannerPrompts
import com.example.robboss_mobile_app.model.MaskFacts
import com.example.robboss_mobile_app.model.MaskFile
import com.example.robboss_mobile_app.model.MixDraft
import com.example.robboss_mobile_app.model.Palette
import com.example.robboss_mobile_app.model.PlanDraft
import com.example.robboss_mobile_app.model.Rules
import com.example.robboss_mobile_app.model.StepDraft
import com.example.robboss_mobile_app.model.describeMask
import kotlinx.serialization.json.JsonObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

class PlannerTest {
    @Test
    fun promptNamesEveryMaskAndThePalette() {
        val facts = listOf(
            MaskFacts("01_darkest", 40.0, listOf(20, 30, 40), listOf(0, 50)),
            MaskFacts("02_dark", 25.5, listOf(90, 80, 70), listOf(40, 99)),
        ).joinToString("\n") { it.toJsonLine() }
        val prompt = PlannerPrompts.lesson(2, facts, "")
        assertTrue(prompt.contains("01_darkest"))
        assertTrue(prompt.contains("02_dark"))
        Palette.pigments.forEach { assertTrue(prompt.contains(it)) }
        Palette.brushes.forEach { assertTrue(prompt.contains(it)) }
        assertTrue(prompt.contains("Total parts at most ${Rules.MAX_TOTAL_PARTS}"))
    }

    @Test
    fun rejectsUnknownPigmentAndTooManyParts() {
        val ids = listOf("01_darkest")
        val unknown = draft(pigment = "glitter", parts = listOf(1))
        assertTrue(Rules.validateDraft(unknown, ids).any { "glitter" in it })
        val tooMany = draft(pigment = "titanium white", parts = listOf(13))
        assertTrue(Rules.validateDraft(tooMany, ids).any { "parts" in it })
        val total = draft(pigment = "titanium white", parts = listOf(7, 6), second = "ivory black")
        assertTrue(Rules.validateDraft(total, ids).any { "total parts" in it })
    }

    @Test
    fun retriesUntilTheDraftIsValid() {
        val gemini = ScriptedGemini(
            listOf(
                """{"steps":[${stepJson("01_darkest", "glitter", 2)}]}""",
                """{"steps":[${stepJson("01_darkest", "ultramarine blue", 3)}]}""",
            ),
        )
        val steps = Planner(gemini).plan(byteArrayOf(1), listOf(mask("01_darkest")))
        assertEquals(2, gemini.calls)
        assertEquals("ultramarine blue", steps.single().mix.single().pigment)
        assertEquals(listOf(10, 20, 30), steps.single().targetRgb)
    }

    @Test
    fun describeMaskMeasuresTheWhiteRegion() {
        val width = 2
        val height = 2
        val white = 0xFFFFFFFF.toInt()
        val black = 0xFF000000.toInt()
        val mask = intArrayOf(white, black, white, black)
        val ref = intArrayOf(0xFF0A141E.toInt(), 0, 0xFF0A141E.toInt(), 0)
        val facts = describeMask(ref, mask, width, height, "sky")
        assertEquals(50.0, facts.coveragePct, 0.001)
        assertEquals(listOf(10, 20, 30), facts.meanRgb)
        assertEquals(listOf(0, 50), facts.verticalExtentPct)
    }

    private fun mask(id: String) = MaskFile(
        relativePath = "layers/$id.png",
        png = byteArrayOf(1),
        facts = MaskFacts(id, 10.0, listOf(10, 20, 30), listOf(0, 10)),
    )

    private fun draft(pigment: String, parts: List<Int>, second: String? = null): PlanDraft {
        val mix = parts.mapIndexed { index, count ->
            MixDraft(if (index == 0) pigment else second!!, count)
        }
        return PlanDraft(
            listOf(
                StepDraft(
                    maskId = "01_darkest",
                    name = "Sky",
                    mix = mix,
                    brush = "1in flat",
                    technique = "Lay in a flat wash.",
                    strokeDirDeg = 0,
                    success = "Even coverage.",
                ),
            ),
        )
    }

    private fun stepJson(id: String, pigment: String, parts: Int) =
        """{"mask_id":"$id","name":"Sky","mix":[{"pigment":"$pigment","parts":$parts}],"brush":"1in flat","technique":"Lay in a flat wash.","stroke_dir_deg":0,"success":"Even coverage."}"""
}

private class ScriptedGemini(private val replies: List<String>) : GeminiCompleter {
    var calls = 0
    override fun complete(parts: List<GeminiPart>, schema: JsonObject): String = replies[calls++]
}
