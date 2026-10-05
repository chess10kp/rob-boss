package com.example.robboss_mobile_app.ui

import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.ExperimentalLayoutApi
import androidx.compose.foundation.layout.FlowRow
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.IntrinsicSize
import androidx.compose.foundation.layout.fillMaxHeight
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextDecoration
import androidx.compose.ui.unit.dp
import com.example.robboss_mobile_app.domain.UiState
import com.example.robboss_mobile_app.model.Palette
import com.example.robboss_mobile_app.ui.theme.LocalRobBossColors
import com.example.robboss_mobile_app.ui.theme.pigmentColor

@OptIn(ExperimentalLayoutApi::class)
@Composable
fun LessonScreen(
    state: UiState,
    onBack: () -> Unit,
    onNext: () -> Unit,
    onOutline: () -> Unit,
    onCheck: () -> Unit,
    onQuit: () -> Unit,
) {
    val colors = LocalRobBossColors.current
    val step = state.steps.getOrNull(state.position) ?: return
    val reference = state.reference
    Column(verticalArrangement = Arrangement.spacedBy(16.dp)) {
        BossCard {
            Kicker("On your canvas now")
            if (reference != null) {
                StepPreview(
                    reference = reference,
                    mask = state.masks[step.maskId],
                    outline = state.outline,
                    accent = colors.accent,
                )
            }
        }
        BossCard {
            Kicker("Step ${step.index} of ${state.steps.size}")
            Text(step.name, style = MaterialTheme.typography.titleLarge, modifier = Modifier.padding(bottom = 4.dp))
            FieldLabel("Let's mix up")
            FlowRow(horizontalArrangement = Arrangement.spacedBy(8.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
                step.mix.forEach { part ->
                    Row(
                        Modifier
                            .border(1.dp, colors.line, CircleShape)
                            .padding(start = 4.dp, end = 12.dp, top = 4.dp, bottom = 4.dp),
                        verticalAlignment = Alignment.CenterVertically,
                    ) {
                        Box(
                            Modifier
                                .size(26.dp)
                                .clip(CircleShape)
                                .background(pigmentColor(Palette.pigmentRgb(part.pigment)))
                                .border(1.dp, colors.ink.copy(alpha = 0.2f), CircleShape),
                        )
                        Text("  ${part.parts} × ${part.pigment}")
                    }
                }
            }
            val rgb = step.targetRgb
            Row(Modifier.padding(top = 8.dp), verticalAlignment = Alignment.CenterVertically) {
                if (rgb.size == 3) {
                    Box(
                        Modifier
                            .size(36.dp)
                            .clip(RoundedCornerShape(8.dp))
                            .background(
                                androidx.compose.ui.graphics.Color(
                                    rgb[0] / 255f,
                                    rgb[1] / 255f,
                                    rgb[2] / 255f,
                                ),
                            )
                            .border(1.dp, colors.line, RoundedCornerShape(8.dp)),
                    )
                }
                Text(
                    "  the colour we're after · brush: ${step.brush}",
                    color = colors.muted,
                    style = MaterialTheme.typography.bodyLarge,
                )
            }
            FieldLabel("How we'll do it")
            Text(step.technique)
            FieldLabel("You'll know it's done when")
            Text(step.success)
            if (!state.status.isNullOrBlank()) {
                val tone = if (state.tone == "done") colors.ok else colors.warn
                Row(
                    Modifier
                        .padding(top = 14.dp)
                        .height(IntrinsicSize.Min)
                        .clip(RoundedCornerShape(8.dp))
                        .background(tone.copy(alpha = 0.12f)),
                ) {
                    Box(Modifier.width(4.dp).fillMaxHeight().background(tone))
                    Text(
                        state.status,
                        color = colors.ink,
                        fontWeight = FontWeight.SemiBold,
                        modifier = Modifier.padding(12.dp),
                    )
                }
            }
            Row(Modifier.padding(top = 16.dp), horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                BossButton("← Back", onBack, enabled = state.position > 0 && !state.checking)
                BossButton("Next →", onNext, enabled = !state.checking)
            }
            Row(Modifier.padding(top = 8.dp), horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                BossButton(
                    if (state.checking) "Taking a look…" else "Check my work",
                    onCheck,
                    primary = true,
                    enabled = !state.checking,
                )
                BossButton("Outline: ${if (state.outline) "on" else "off"}", onOutline, enabled = !state.checking)
            }
            BossButton("Quit", onQuit, modifier = Modifier.padding(top = 8.dp), enabled = !state.checking)
            Text(
                "Take a photo of the canvas when the step is done, or when you want a correction.",
                color = colors.muted,
                style = MaterialTheme.typography.labelSmall.copy(letterSpacing = androidx.compose.ui.unit.TextUnit.Unspecified),
                modifier = Modifier.padding(top = 10.dp),
            )
        }
        LessonList(state)
    }
}

@Composable
private fun LessonList(state: UiState) {
    var open by rememberSaveable { mutableStateOf(true) }
    BossCard {
        Kicker("Today's lesson")
        BossButton(if (open) "Hide the list" else "Show the list", { open = !open }, modifier = Modifier.padding(top = 8.dp))
        if (open) {
            state.steps.forEach { step ->
                val current = step.index == state.steps.getOrNull(state.position)?.index
                val done = step.index < (state.steps.getOrNull(state.position)?.index ?: 0)
                val colors = LocalRobBossColors.current
                Text(
                    "${step.index}. ${step.name}",
                    color = if (current) colors.ink else colors.muted,
                    fontWeight = if (current) FontWeight.Bold else FontWeight.Normal,
                    textDecoration = if (done) TextDecoration.LineThrough else null,
                    modifier = Modifier.padding(top = 6.dp),
                )
            }
        }
    }
}

@Composable
private fun FieldLabel(text: String) {
    Text(text, fontWeight = FontWeight.SemiBold, modifier = Modifier.padding(top = 12.dp, bottom = 4.dp))
}

@Composable
fun DoneScreen(message: String?, onAgain: () -> Unit, onQuit: () -> Unit) {
    val colors = LocalRobBossColors.current
    BossCard {
        Kicker("RobBoss")
        Text("That's all for today", style = MaterialTheme.typography.titleLarge)
        Text(
            message ?: "Your painting's finished - happy painting, friend.",
            color = colors.muted,
            modifier = Modifier.padding(top = 8.dp),
        )
        Row(Modifier.padding(top = 16.dp), horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            BossButton("Paint another", onAgain, primary = true)
            BossButton("Quit", onQuit)
        }
    }
}
