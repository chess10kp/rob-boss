package com.example.robboss_mobile_app

import com.example.robboss_mobile_app.model.Rules
import com.example.robboss_mobile_app.model.Verdict
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class CriticTest {
    @Test
    fun tieOrMinorityAdjustBecomesReady() {
        val ready = Verdict("READY", "none", "")
        val dark = Verdict("ADJUST", "value", "Lighten the sky a little.")
        assertEquals("READY", Rules.vote(listOf(ready, dark)).verdict)
        assertEquals("READY", Rules.vote(listOf(ready, dark, ready)).verdict)
    }

    @Test
    fun majorityAdjustKeepsTheMostCommonCategory() {
        val dark = Verdict("ADJUST", "value", "Lighten it.")
        val patchy = Verdict("ADJUST", "coverage", "Fill the bare spots.")
        val ready = Verdict("READY", "none", "")
        val voted = Rules.vote(listOf(dark, patchy, dark, ready))
        assertEquals("ADJUST", voted.verdict)
        assertEquals("value", voted.category)
        assertEquals("Lighten it.", voted.adjustment)
    }

    @Test
    fun emptyVoteFails() {
        try {
            Rules.vote(emptyList())
            error("expected a failure")
        } catch (error: IllegalStateException) {
            assertTrue(error.message!!.contains("no valid samples"))
        }
    }

    @Test
    fun verdictConsistency() {
        assertTrue(Rules.verdictIsConsistent(Verdict("READY", "none", "")))
        assertTrue(Rules.verdictIsConsistent(Verdict("READY", "none", "   ")))
        assertFalse(Rules.verdictIsConsistent(Verdict("READY", "value", "")))
        assertFalse(Rules.verdictIsConsistent(Verdict("READY", "none", "keep going")))
        assertFalse(Rules.verdictIsConsistent(Verdict("ADJUST", "none", "fix it")))
        assertFalse(Rules.verdictIsConsistent(Verdict("ADJUST", "value", "  ")))
        assertTrue(Rules.verdictIsConsistent(Verdict("ADJUST", "coverage", "Fill the bare spots.")))
    }
}
