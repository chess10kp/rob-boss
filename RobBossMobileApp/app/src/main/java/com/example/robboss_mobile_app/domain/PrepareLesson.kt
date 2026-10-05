package com.example.robboss_mobile_app.domain

import android.graphics.Bitmap
import android.graphics.BitmapFactory
import com.example.robboss_mobile_app.data.GimpApi
import com.example.robboss_mobile_app.data.argb
import com.example.robboss_mobile_app.data.decodeSampled
import com.example.robboss_mobile_app.data.jpgName
import com.example.robboss_mobile_app.data.toJpeg
import com.example.robboss_mobile_app.model.MaskFile
import com.example.robboss_mobile_app.model.Step
import com.example.robboss_mobile_app.model.describeMask
import java.io.File

data class PreparedLesson(
    val steps: List<Step>,
    val reference: Bitmap,
    val masks: Map<String, Bitmap>,
    val refJpeg: ByteArray,
    val maskPng: Map<String, ByteArray>,
)

/**
 * Upload, download, then plan. [report] receives stage index, state
 * (running/done), and a short detail line.
 */
class PrepareLesson(
    private val gimp: GimpApi,
    private val planner: Planner,
    private val cacheRoot: File,
    private val geminiKey: String,
) {
    fun run(image: ByteArray, filename: String, report: (Int, String, String) -> Unit): PreparedLesson {
        report(0, "running", "")
        if (gimp.baseUrl.isBlank()) {
            error("No GIMP API URL. Set ROB_BOSS_API_URL in .env or BASE_URL in REMOTE_API.md, then rebuild.")
        }
        if (gimp.token.isBlank()) error("No GIMP API token. Set AGENT_API_TOKEN in .env, then rebuild.")
        val health = gimp.health()
        report(0, "done", health)

        report(1, "running", "sending the picture")
        val reference = decodeSampled(image, 1024)
        val jpeg = reference.toJpeg(88)
        val job = gimp.process(jpeg, jpgName(filename), "image/jpeg")
        if (job.steps.isEmpty()) error("GIMP returned no layers")
        report(1, "done", "${job.steps.size} layers")

        report(2, "running", "")
        val scene = File(cacheRoot, job.jobId).apply { mkdirs() }
        val downloaded = gimp.downloadMasks(job, scene)
        report(2, "done", "${downloaded.size} masks")

        report(3, "running", "asking for the lesson")
        if (geminiKey.isBlank()) error("Set GEMINI_API_KEY in .env, then rebuild.")
        val maskBitmaps = linkedMapOf<String, Bitmap>()
        val maskBytes = linkedMapOf<String, ByteArray>()
        val files = downloaded.map { item ->
            val bitmap = BitmapFactory.decodeByteArray(item.bytes, 0, item.bytes.size)
                ?: error("could not read ${item.relativePath}")
            val scaled = if (reference.width == bitmap.width && reference.height == bitmap.height) {
                reference
            } else {
                Bitmap.createScaledBitmap(reference, bitmap.width, bitmap.height, true)
            }
            val id = item.file.nameWithoutExtension
            val facts = describeMask(scaled.argb(), bitmap.argb(), bitmap.width, bitmap.height, id)
            maskBitmaps[id] = bitmap
            maskBytes[id] = item.bytes
            MaskFile(item.relativePath, item.bytes, facts)
        }
        val steps = planner.plan(jpeg, files)
        report(3, "done", "${steps.size} steps")
        return PreparedLesson(steps, reference, maskBitmaps, jpeg, maskBytes)
    }
}
