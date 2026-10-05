package com.example.robboss_mobile_app.data

import okhttp3.OkHttpClient
import okhttp3.Request
import java.util.concurrent.TimeUnit

class ApiException(message: String) : RuntimeException(message)

fun longHttpClient(): OkHttpClient = OkHttpClient.Builder()
    .connectTimeout(30, TimeUnit.SECONDS)
    .readTimeout(10, TimeUnit.MINUTES)
    .writeTimeout(2, TimeUnit.MINUTES)
    .build()

fun OkHttpClient.executeBytes(request: Request): ByteArray {
    newCall(request).execute().use { response ->
        val body = response.body?.bytes() ?: ByteArray(0)
        if (!response.isSuccessful) {
            val snippet = body.toString(Charsets.UTF_8).take(300)
            throw ApiException("${response.request.url.encodedPath}: HTTP ${response.code} $snippet")
        }
        return body
    }
}
