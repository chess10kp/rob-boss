package com.example.robboss_mobile_app.ui.theme

import androidx.compose.foundation.isSystemInDarkTheme
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.darkColorScheme
import androidx.compose.material3.lightColorScheme
import androidx.compose.runtime.Composable
import androidx.compose.runtime.CompositionLocalProvider
import androidx.compose.runtime.staticCompositionLocalOf

val LocalRobBossColors = staticCompositionLocalOf { LightRobBossColors }

private fun lightScheme(colors: RobBossColors) = lightColorScheme(
    primary = colors.accent,
    onPrimary = colors.onAccent,
    background = colors.bg,
    onBackground = colors.ink,
    surface = colors.card,
    onSurface = colors.ink,
    outline = colors.line,
    error = colors.bad,
)

private fun darkScheme(colors: RobBossColors) = darkColorScheme(
    primary = colors.accent,
    onPrimary = colors.onAccent,
    background = colors.bg,
    onBackground = colors.ink,
    surface = colors.card,
    onSurface = colors.ink,
    outline = colors.line,
    error = colors.bad,
)

@Composable
fun RobBossmobileappTheme(
    darkTheme: Boolean = isSystemInDarkTheme(),
    content: @Composable () -> Unit,
) {
    val colors = if (darkTheme) DarkRobBossColors else LightRobBossColors
    CompositionLocalProvider(LocalRobBossColors provides colors) {
        MaterialTheme(
            colorScheme = if (darkTheme) darkScheme(colors) else lightScheme(colors),
            typography = Typography,
            content = content,
        )
    }
}
