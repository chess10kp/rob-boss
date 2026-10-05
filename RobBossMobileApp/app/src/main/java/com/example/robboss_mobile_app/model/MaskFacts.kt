package com.example.robboss_mobile_app.model

/** Measured region facts, matching describe_mask in track2/planner.py. */
data class MaskFacts(
    val maskId: String,
    val coveragePct: Double,
    val meanRgb: List<Int>,
    val verticalExtentPct: List<Int>,
) {
    fun toJsonLine(): String {
        val rgb = meanRgb.joinToString(", ")
        val extent = verticalExtentPct.joinToString(", ")
        return """{"mask_id": ${jsonString(maskId)}, "coverage_pct": $coveragePct, "mean_rgb": [$rgb], "vertical_extent_pct": [$extent]}"""
    }
}

data class MaskFile(
    val relativePath: String,
    val png: ByteArray,
    val facts: MaskFacts,
)

fun describeMask(
    refArgb: IntArray,
    maskArgb: IntArray,
    width: Int,
    height: Int,
    maskId: String,
): MaskFacts {
    require(refArgb.size == width * height && maskArgb.size == width * height)
    var count = 0
    var sumR = 0L
    var sumG = 0L
    var sumB = 0L
    var minY = Int.MAX_VALUE
    var maxY = -1
    for (y in 0 until height) {
        var row = false
        for (x in 0 until width) {
            val i = y * width + x
            if (!isMaskOn(maskArgb[i])) continue
            row = true
            count++
            val pixel = refArgb[i]
            sumR += (pixel shr 16) and 0xFF
            sumG += (pixel shr 8) and 0xFF
            sumB += pixel and 0xFF
        }
        if (row) {
            if (y < minY) minY = y
            if (y > maxY) maxY = y
        }
    }
    val total = width * height
    val coverage = if (total == 0) 0.0 else round1(count * 100.0 / total)
    val mean = if (count == 0) {
        listOf(0, 0, 0)
    } else {
        listOf((sumR / count).toInt(), (sumG / count).toInt(), (sumB / count).toInt())
    }
    val extent = if (maxY < 0) listOf(0, 0) else listOf(minY * 100 / height, maxY * 100 / height)
    return MaskFacts(maskId, coverage, mean, extent)
}

fun isMaskOn(pixel: Int): Boolean {
    val r = (pixel shr 16) and 0xFF
    val g = (pixel shr 8) and 0xFF
    val b = pixel and 0xFF
    return r >= 250 && g >= 250 && b >= 250
}

private fun round1(value: Double): Double = kotlin.math.round(value * 10.0) / 10.0

internal fun jsonString(value: String): String =
    kotlinx.serialization.json.Json.encodeToString(kotlinx.serialization.serializer<String>(), value)
