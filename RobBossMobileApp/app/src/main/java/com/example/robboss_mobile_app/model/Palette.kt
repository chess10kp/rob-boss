package com.example.robboss_mobile_app.model

/** Paints and brushes from track2/palette.py, plus the panel swatch colours. */
object Palette {
    val pigments = listOf(
        "titanium white",
        "ivory black",
        "ultramarine blue",
        "cadmium yellow",
        "cadmium red",
        "burnt umber",
    )

    val brushes = listOf("1in flat", "#6 round")

    /** 24-bit RGB, matching PIGMENT_RGB in track1/panel.py. */
    fun pigmentRgb(name: String): Int = when (name) {
        "titanium white" -> 0xF5F5F0
        "ivory black" -> 0x1E1E1E
        "ultramarine blue" -> 0x1E32A0
        "cadmium yellow" -> 0xFAC814
        "cadmium red" -> 0xC81E1E
        "burnt umber" -> 0x5A3723
        else -> 0x888888
    }
}
