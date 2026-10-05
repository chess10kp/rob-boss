package com.example.robboss_mobile_app.data

import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.buildJsonArray
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.contentOrNull
import kotlinx.serialization.json.jsonArray
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
import kotlinx.serialization.json.put
import okhttp3.HttpUrl.Companion.toHttpUrlOrNull
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import java.util.Base64

sealed class GeminiPart {
    data class Text(val text: String) : GeminiPart()
    data class Jpeg(val bytes: ByteArray) : GeminiPart()
    data class Png(val bytes: ByteArray) : GeminiPart()
}

fun interface GeminiCompleter {
    fun complete(parts: List<GeminiPart>, schema: JsonObject): String
}

/**
 * REST generateContent with JSON schema. Retries overload and rate-limit responses,
 * the same cases track2/gemini.py retries.
 */
class GeminiClient(
    private val http: OkHttpClient,
    private val apiKey: String,
    private val model: String,
    private val baseUrl: String = "https://generativelanguage.googleapis.com/v1beta",
    private val attempts: Int = 3,
    private val sleep: (Long) -> Unit = { Thread.sleep(it) },
) : GeminiCompleter {
    override fun complete(parts: List<GeminiPart>, schema: JsonObject): String {
        var last: Exception? = null
        repeat(attempts) { attempt ->
            try {
                return request(parts, schema)
            } catch (error: ApiException) {
                last = error
                if (!error.message.orEmpty().let { msg -> RETRYABLE.any { code -> "HTTP $code" in msg } }) throw error
                if (attempt == attempts - 1) throw error
                sleep(5_000L * (1L shl attempt))
            }
        }
        throw last ?: ApiException("Gemini returned no text")
    }

    private fun request(parts: List<GeminiPart>, schema: JsonObject): String {
        val payload = buildJsonObject {
            put("contents", buildJsonArray {
                add(buildJsonObject {
                    put("role", "user")
                    put("parts", buildJsonArray {
                        parts.forEach { part -> add(partJson(part)) }
                    })
                })
            })
            put("generationConfig", buildJsonObject {
                put("temperature", 0.2)
                put("responseMimeType", "application/json")
                put("responseSchema", schema)
            })
        }
        val url = "$baseUrl/models/$model:generateContent".toHttpUrlOrNull()?.newBuilder()
            ?.addQueryParameter("key", apiKey)
            ?.build()
            ?: throw ApiException("bad Gemini URL")
        val request = Request.Builder()
            .url(url)
            .post(payload.toString().toRequestBody(JSON))
            .build()
        val body = http.executeBytes(request).toString(Charsets.UTF_8)
        return extractText(body)
    }
}

private val JSON = "application/json".toMediaType()
private val RETRYABLE = listOf(429, 500, 502, 503, 504)

private fun partJson(part: GeminiPart) = when (part) {
    is GeminiPart.Text -> buildJsonObject { put("text", part.text) }
    is GeminiPart.Jpeg -> inline("image/jpeg", part.bytes)
    is GeminiPart.Png -> inline("image/png", part.bytes)
}

private fun inline(mime: String, bytes: ByteArray) = buildJsonObject {
    put("inlineData", buildJsonObject {
        put("mimeType", mime)
        put("data", Base64.getEncoder().encodeToString(bytes))
    })
}

internal fun extractText(body: String): String {
    val root = Json.parseToJsonElement(body).jsonObject
    val parts = root["candidates"]?.jsonArray?.firstOrNull()?.jsonObject
        ?.get("content")?.jsonObject
        ?.get("parts")?.jsonArray
        ?: throw ApiException("Gemini returned no text")
    val text = parts.mapNotNull { it.jsonObject["text"]?.jsonPrimitive?.contentOrNull }.joinToString("")
    if (text.isBlank()) throw ApiException("Gemini returned no text")
    return stripFence(text)
}

internal fun stripFence(text: String): String {
    val trimmed = text.trim()
    if (!trimmed.startsWith("```")) return trimmed
    return trimmed.removePrefix("```json").removePrefix("```").removeSuffix("```").trim()
}
