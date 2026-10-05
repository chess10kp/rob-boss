package com.example.robboss_mobile_app.ui

import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.ColumnScope
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.dp
import com.example.robboss_mobile_app.ui.theme.LocalRobBossColors
import androidx.compose.material3.Button
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.MaterialTheme

private val CardShape = RoundedCornerShape(12.dp)
private val ButtonShape = RoundedCornerShape(8.dp)

@Composable
fun BossCard(modifier: Modifier = Modifier, content: @Composable ColumnScope.() -> Unit) {
    val colors = LocalRobBossColors.current
    Column(
        modifier
            .fillMaxWidth()
            .clip(CardShape)
            .background(colors.card)
            .border(1.dp, colors.line, CardShape)
            .padding(16.dp),
        content = content,
    )
}

@Composable
fun Kicker(text: String) {
    Text(
        text = text.uppercase(),
        style = MaterialTheme.typography.labelSmall,
        color = LocalRobBossColors.current.muted,
    )
}

@Composable
fun BossButton(
    text: String,
    onClick: () -> Unit,
    modifier: Modifier = Modifier,
    primary: Boolean = false,
    enabled: Boolean = true,
) {
    val colors = LocalRobBossColors.current
    Button(
        onClick = onClick,
        enabled = enabled,
        modifier = modifier,
        shape = ButtonShape,
        colors = if (primary) {
            ButtonDefaults.buttonColors(containerColor = colors.accent, contentColor = colors.onAccent)
        } else {
            ButtonDefaults.buttonColors(
                containerColor = colors.card,
                contentColor = colors.ink,
                disabledContainerColor = colors.card,
                disabledContentColor = colors.muted,
            )
        },
        border = androidx.compose.foundation.BorderStroke(1.dp, if (primary) colors.accent else colors.line),
    ) {
        Text(text, fontWeight = if (primary) FontWeight.SemiBold else FontWeight.Normal, textAlign = TextAlign.Center)
    }
}
