package com.example.robboss_mobile_app.ui

import androidx.compose.animation.core.RepeatMode
import androidx.compose.animation.core.animateFloat
import androidx.compose.animation.core.infiniteRepeatable
import androidx.compose.animation.core.rememberInfiniteTransition
import androidx.compose.animation.core.tween
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.width
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.dp
import com.example.robboss_mobile_app.domain.StageUi
import com.example.robboss_mobile_app.ui.theme.LocalRobBossColors

@Composable
fun WorkingScreen(stages: List<StageUi>, onQuit: () -> Unit) {
    val colors = LocalRobBossColors.current
    val pulse by rememberInfiniteTransition(label = "pulse").animateFloat(
        initialValue = 0.35f,
        targetValue = 1f,
        animationSpec = infiniteRepeatable(tween(1000), RepeatMode.Reverse),
        label = "pulse-alpha",
    )
    BossCard {
        Kicker("Getting ready")
        Text("Let's get our paints ready", style = MaterialTheme.typography.titleLarge)
        Column(Modifier.padding(top = 8.dp)) {
            stages.forEach { stage ->
                val tint = when (stage.state) {
                    "done" -> colors.ok
                    "failed" -> colors.bad
                    "running" -> colors.accent
                    else -> colors.muted
                }
                val icon = when (stage.state) {
                    "done" -> "✓"
                    "failed" -> "✕"
                    "skipped" -> "–"
                    "running" -> "●"
                    else -> "○"
                }
                Row(Modifier.padding(vertical = 10.dp)) {
                    Text(
                        icon,
                        color = tint.copy(alpha = if (stage.state == "running") pulse else 1f),
                        fontWeight = FontWeight.Bold,
                        textAlign = TextAlign.Center,
                        modifier = Modifier.width(22.dp),
                    )
                    Column(Modifier.padding(start = 12.dp)) {
                        Text(stage.name, color = if (stage.state == "pending") colors.muted else colors.ink)
                        if (stage.detail.isNotBlank()) {
                            Text(stage.detail, color = colors.muted, style = MaterialTheme.typography.labelSmall.copy(letterSpacing = androidx.compose.ui.unit.TextUnit.Unspecified))
                        }
                    }
                }
            }
        }
        BossButton("Cancel", onQuit, modifier = Modifier.padding(top = 8.dp))
    }
}
