package com.example.robboss_mobile_app

import com.example.robboss_mobile_app.data.ApiException
import com.example.robboss_mobile_app.data.GeminiClient
import com.example.robboss_mobile_app.data.GeminiPart
import com.example.robboss_mobile_app.domain.verdictSchema
import okhttp3.OkHttpClient
import okhttp3.mockwebserver.MockResponse
import okhttp3.mockwebserver.MockWebServer
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Test

class GeminiClientTest {
    private val server = MockWebServer()

    @Before
    fun setUp() {
        server.start()
    }

    @After
    fun tearDown() {
        server.shutdown()
    }

    @Test
    fun readsTheCandidateTextAndSendsTheKey() {
        server.enqueue(
            MockResponse().setBody(
                """{"candidates":[{"content":{"parts":[{"text":"{\"verdict\":\"READY\",\"category\":\"none\",\"adjustment\":\"\"}"}]}}]}""",
            ),
        )
        val text = client().complete(listOf(GeminiPart.Text("look")), verdictSchema())
        assertTrue(text.contains("READY"))
        val request = server.takeRequest()
        assertTrue(request.path!!.contains("generateContent"))
        assertTrue(request.path!!.contains("key=test-key"))
        assertTrue(request.body.readUtf8().contains("responseSchema"))
    }

    @Test
    fun retriesOverloadThenReturnsText() {
        server.enqueue(MockResponse().setResponseCode(503).setBody("busy"))
        server.enqueue(
            MockResponse().setBody(
                """{"candidates":[{"content":{"parts":[{"text":"{\"ok\":true}"}]}}]}""",
            ),
        )
        val text = client(attempts = 2).complete(listOf(GeminiPart.Jpeg(byteArrayOf(1))), verdictSchema())
        assertEquals("""{"ok":true}""", text)
        assertEquals(2, server.requestCount)
    }

    @Test
    fun clientErrorsAreNotRetried() {
        server.enqueue(MockResponse().setResponseCode(400).setBody("bad"))
        try {
            client(attempts = 3).complete(listOf(GeminiPart.Text("x")), verdictSchema())
            error("expected ApiException")
        } catch (error: ApiException) {
            assertTrue(error.message!!.contains("HTTP 400"))
        }
        assertEquals(1, server.requestCount)
    }

    private fun client(attempts: Int = 1) = GeminiClient(
        http = OkHttpClient(),
        apiKey = "test-key",
        model = "gemini-test",
        baseUrl = server.url("/").toString().trimEnd('/'),
        attempts = attempts,
        sleep = {},
    )
}