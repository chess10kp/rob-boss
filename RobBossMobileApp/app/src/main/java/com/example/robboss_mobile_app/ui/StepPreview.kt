package com.example.robboss_mobile_app.ui

import android.graphics.Bitmap
import androidx.compose.foundation.Image
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.runtime.Composable
import androidx.compose.runtime.remember
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.asImageBitmap
import androidx.compose.ui.layout.ContentScale
import androidx.compose.ui.unit.dp
import com.example.robboss_mobile_app.model.isMaskOn

@Composable
fun StepPreview(reference: Bitmap, mask: Bitmap?, outline: Boolean, accent: Color) {
    val overlay = remember(mask, outline, accent) {
        mask?.let { maskOverlay(it, accent, outline) }?.asImageBitmap()
    }
    Box(
        Modifier
            .fillMaxWidth()
            .clip(RoundedCornerShape(8.dp))
            .background(Color.Black),
    ) {
        Image(
            bitmap = reference.asImageBitmap(),
            contentDescription = "This step on the reference",
            modifier = Modifier.fillMaxWidth(),
            contentScale = ContentScale.FillWidth,
        )
        if (overlay != null) {
            Image(
                bitmap = overlay,
                contentDescription = null,
                modifier = Modifier.fillMaxWidth(),
                contentScale = ContentScale.FillWidth,
            )
        }
    }
}

private fun maskOverlay(mask: Bitmap, accent: Color, outline: Boolean): Bitmap {
    val width = mask.width
    val height = mask.height
    val src = IntArray(width * height)
    mask.getPixels(src, 0, width, 0, 0, width, height)
    val on = BooleanArray(src.size) { isMaskOn(src[it]) }
    val fill = accent.copy(alpha = 0.4f).toArgb()
    val edge = accent.copy(alpha = 1f).toArgb()
    val out = IntArray(src.size)
    for (y in 0 until height) {
        for (x in 0 until width) {
            val i = y * width + x
            if (!on[i]) continue
            val border = x == 0 || y == 0 || x == width - 1 || y == height - 1 ||
                !on[i - 1] || !on[i + 1] || !on[i - width] || !on[i + width]
            if (outline) {
                if (border) out[i] = edge
            } else if (border) {
                out[i] = edge
            } else {
                out[i] = fill
            }
        }
    }
    return Bitmap.createBitmap(out, width, height, Bitmap.Config.ARGB_8888)
}

private fun Color.toArgb(): Int {
    val a = (alpha * 255).toInt().coerceIn(0, 255)
    val r = (red * 255).toInt().coerceIn(0, 255)
    val g = (green * 255).toInt().coerceIn(0, 255)
    val b = (blue * 255).toInt().coerceIn(0, 255)
    return (a shl 24) or (r shl 16) or (g shl 8) or b
}
