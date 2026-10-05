package com.example.robboss_mobile_app.data

import android.graphics.Bitmap
import android.graphics.BitmapFactory
import java.io.ByteArrayOutputStream
import kotlin.math.max
import kotlin.math.roundToInt

fun decodeSampled(bytes: ByteArray, maxEdge: Int): Bitmap {
    val bounds = BitmapFactory.Options().apply { inJustDecodeBounds = true }
    BitmapFactory.decodeByteArray(bytes, 0, bytes.size, bounds)
    var sample = 1
    val edge = max(bounds.outWidth, bounds.outHeight)
    while (edge / sample > maxEdge * 2 && sample < edge) sample *= 2
    val decoded = BitmapFactory.decodeByteArray(
        bytes,
        0,
        bytes.size,
        BitmapFactory.Options().apply { inSampleSize = sample },
    ) ?: throw IllegalArgumentException("I can't quite read that picture - try a JPG or PNG")
    return decoded.fitLongest(maxEdge)
}

fun Bitmap.fitLongest(maxEdge: Int): Bitmap {
    val edge = max(width, height)
    if (edge <= maxEdge) return this
    val scale = maxEdge.toFloat() / edge
    return Bitmap.createScaledBitmap(this, (width * scale).roundToInt().coerceAtLeast(1), (height * scale).roundToInt().coerceAtLeast(1), true)
}

fun Bitmap.toJpeg(quality: Int): ByteArray {
    val stream = ByteArrayOutputStream()
    compress(Bitmap.CompressFormat.JPEG, quality, stream)
    return stream.toByteArray()
}

fun Bitmap.argb(): IntArray {
    val pixels = IntArray(width * height)
    getPixels(pixels, 0, width, 0, 0, width, height)
    return pixels
}

fun jpgName(filename: String): String {
    val base = filename.substringBeforeLast('.').ifBlank { "reference" }
    return "$base.jpg"
}
