package com.example.robboss_mobile_app.data

import com.example.robboss_mobile_app.model.AppJson
import com.example.robboss_mobile_app.model.GimpJob
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import java.io.File

data class DownloadedMask(
    val relativePath: String,
    val file: File,
    val bytes: ByteArray,
)

/** Client for the remote GIMP API in server.py / REMOTE_API.md. */
class GimpApi(
    private val http: OkHttpClient,
    val baseUrl: String,
    val token: String,
) {
    private val root = baseUrl.trimEnd('/')

    fun health(): String {
        val body = get("/healthz").toString(Charsets.UTF_8)
        val status = runCatching {
            Json.parseToJsonElement(body).jsonObject["status"]?.jsonPrimitive?.content
        }.getOrNull()
        return status ?: "ok"
    }

    fun process(image: ByteArray, filename: String, contentType: String): GimpJob {
        val request = Request.Builder()
            .url("$root/process")
            .header("Authorization", "Bearer $token")
            .header("X-Filename", filename)
            .header("User-Agent", "rob-boss-android")
            .post(image.toRequestBody(contentType.toMediaType()))
            .build()
        val body = http.executeBytes(request).toString(Charsets.UTF_8)
        return AppJson.decodeFromString(GimpJob.serializer(), body)
    }

    fun downloadMasks(job: GimpJob, sceneDir: File): List<DownloadedMask> {
        return job.steps.map { step ->
            val relative = step.path.substringAfter("/scene/", step.path.trimStart('/'))
            val dest = File(sceneDir, relative)
            dest.parentFile?.mkdirs()
            val bytes = get(step.path)
            dest.writeBytes(bytes)
            DownloadedMask(relative, dest, bytes)
        }
    }

    private fun get(path: String): ByteArray {
        val url = root + "/" + path.trimStart('/')
        val request = Request.Builder()
            .url(url)
            .header("Authorization", "Bearer $token")
            .header("User-Agent", "rob-boss-android")
            .get()
            .build()
        return http.executeBytes(request)
    }
}
