package com.example.robboss_mobile_app.ui.theme

import androidx.compose.ui.graphics.Color

/** Panel colours from track1/panel.py, light and dark. */
data class RobBossColors(
    val bg: Color,
    val card: Color,
    val ink: Color,
    val muted: Color,
    val line: Color,
    val accent: Color,
    val ok: Color,
    val warn: Color,
    val bad: Color,
    val onAccent: Color,
)

val LightRobBossColors = RobBossColors(
    bg = Color(0xFFF6F4EF),
    card = Color(0xFFFFFFFF),
    ink = Color(0xFF1D1D1F),
    muted = Color(0xFF6B6B70),
    line = Color(0xFFE2DED6),
    accent = Color(0xFFC2410C),
    ok = Color(0xFF15803D),
    warn = Color(0xFFB45309),
    bad = Color(0xFFB91C1C),
    onAccent = Color(0xFFFFFFFF),
)

val DarkRobBossColors = RobBossColors(
    bg = Color(0xFF141416),
    card = Color(0xFF1E1E22),
    ink = Color(0xFFF2F2F2),
    muted = Color(0xFF9A9AA2),
    line = Color(0xFF2E2E34),
    accent = Color(0xFFFB923C),
    ok = Color(0xFF4ADE80),
    warn = Color(0xFFFBBF24),
    bad = Color(0xFFF87171),
    onAccent = Color(0xFFFFFFFF),
)

fun pigmentColor(rgb: Int): Color = Color(rgb or 0xFF000000.toInt())
