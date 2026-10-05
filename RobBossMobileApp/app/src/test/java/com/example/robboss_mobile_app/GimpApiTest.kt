package com.example.robboss_mobile_app

import com.example.robboss_mobile_app.data.GimpApi
import okhttp3.OkHttpClient
import okhttp3.mockwebserver.MockResponse
import okhttp3.mockwebserver.MockWebServer
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Test
import java.io.File

class GimpApiTest {
    private val server = MockWebServer()
    private val http = OkHttpClient()

    @Before
    fun setUp() {
        server.start()
    }

    @After
    fun tearDown() {
        server.shutdown()
    }

    @Test
    fun healthReadsStatus() {
        server.enqueue(MockResponse().setBody("""{"status":"ok","service":"rob-boss-agent"}"""))
        val api = api()
        assertEquals("ok", api.health())
        val request = server.takeRequest()
        assertEquals("/healthz", request.path)
        assertEquals("Bearer secret-token", request.getHeader("Authorization"))
    }

    @Test
    fun processPostsBytesAndParsesSteps() {
        server.enqueue(
            MockResponse().setBody(
                """
                {
                  "job_id": "abc",
                  "engine": "gimp",
                  "width": 10,
                  "height": 8,
                  "steps": [
                    {
                      "path": "/jobs/abc/scene/layers/01_darkest.png",
                      "target_rgb": [1, 2, 3],
                      "coverage": 0.2,
                      "name": "darkest",
                      "posterized_value": 0
                    }
                  ],
                  "artifacts": {
                    "manifest": "/jobs/abc/scene/manifest.json",
                    "posterized": "/jobs/abc/scene/posterized.png",
                    "contact_sheet": "/jobs/abc/scene/contact-sheet.png"
                  }
                }
                """.trimIndent(),
            ),
        )
        val job = api().process(byteArrayOf(9, 8, 7), "reference.jpg", "image/jpeg")
        val request = server.takeRequest()
        assertEquals("POST", request.method)
        assertEquals("/process", request.path)
        assertEquals("image/jpeg", request.getHeader("Content-Type"))
        assertEquals("reference.jpg", request.getHeader("X-Filename"))
        assertEquals("Bearer secret-token", request.getHeader("Authorization"))
        assertEquals(3L, request.bodySize)
        assertEquals("abc", job.jobId)
        assertEquals(listOf(1, 2, 3), job.steps.single().targetRgb)
        assertEquals("/jobs/abc/scene/contact-sheet.png", job.artifacts.contactSheet)
    }

    @Test
    fun downloadSavesMaskBytes() {
        server.enqueue(
            MockResponse().setBody(
                """
                {"job_id":"abc","steps":[{"path":"/jobs/abc/scene/layers/01_darkest.png","target_rgb":[4,5,6]}],"artifacts":{}}
                """.trimIndent(),
            ),
        )
        val job = api().process(byteArrayOf(1), "a.jpg", "image/jpeg")
        server.enqueue(MockResponse().setBody("PNG"))
        val dir = File.createTempFile("scene", "dir")
        dir.delete()
        dir.mkdirs()
        val saved = api().downloadMasks(job, dir)
        val processRequest = server.takeRequest()
        val download = server.takeRequest()
        assertTrue(processRequest.path!!.startsWith("/process"))
        assertEquals("/jobs/abc/scene/layers/01_darkest.png", download.path)
        assertEquals("Bearer secret-token", download.getHeader("Authorization"))
        assertEquals("PNG", saved.single().file.readText())
        assertEquals("layers/01_darkest.png", saved.single().relativePath.replace('\\', '/'))
        dir.deleteRecursively()
    }

    private fun api() = GimpApi(http, server.url("/").toString().trimEnd('/'), "secret-token")
}
