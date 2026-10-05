import java.io.File

plugins {
    alias(libs.plugins.android.application)
    alias(libs.plugins.kotlin.compose)
    alias(libs.plugins.kotlin.serialization)
}

fun parseDotEnv(text: String): Map<String, String> {
    val values = linkedMapOf<String, String>()
    val bare = mutableListOf<String>()
    text.lineSequence().forEach { raw ->
        val line = raw.trim()
        if (line.isEmpty() || line.startsWith("#")) return@forEach
        val eq = line.indexOf('=')
        val key = if (eq > 0) line.substring(0, eq).trim() else ""
        if (eq > 0 && key.matches(Regex("[A-Za-z_][A-Za-z0-9_]*"))) {
            var value = line.substring(eq + 1).trim()
            if (value.length >= 2 &&
                ((value.startsWith("\"") && value.endsWith("\"")) ||
                    (value.startsWith("'") && value.endsWith("'")))
            ) {
                value = value.substring(1, value.length - 1)
            }
            values[key] = value
        } else if (!line.contains(' ') && line.length >= 16) {
            bare += line
        }
    }
    if ("AGENT_API_TOKEN" !in values && bare.isNotEmpty()) {
        values["AGENT_API_TOKEN"] = bare.last()
    }
    return values
}

fun documentedBaseUrl(markdown: String): String =
    Regex("""BASE_URL="(https://[^"]+)"""").find(markdown)?.groupValues?.get(1).orEmpty()

fun javaStringLiteral(value: String): String = buildString {
    append('"')
    for (ch in value) {
        when (ch) {
            '\\' -> append("\\\\")
            '"' -> append("\\\"")
            '\n' -> append("\\n")
            '\r' -> append("\\r")
            else -> append(ch)
        }
    }
    append('"')
}

val repoRoot: File = rootProject.projectDir.parentFile
val env = parseDotEnv(repoRoot.resolve(".env").takeIf { it.isFile }?.readText().orEmpty())
val apiMarkdown = repoRoot.resolve("REMOTE_API.md").takeIf { it.isFile }?.readText().orEmpty()
val geminiKey = env["GEMINI_API_KEY"].orEmpty()
val geminiModel = env["GEMINI_MODEL"]?.takeIf { it.isNotBlank() } ?: "gemini-3.8-flash"
val apiUrl = env["ROB_BOSS_API_URL"]?.takeIf { it.isNotBlank() } ?: documentedBaseUrl(apiMarkdown)
val apiToken = env["AGENT_API_TOKEN"]?.takeIf { it.isNotBlank() }
    ?: env["ROB_BOSS_API_TOKEN"].orEmpty()

android {
    namespace = "com.example.robboss_mobile_app"
    compileSdk {
        version = release(37)
    }

    defaultConfig {
        applicationId = "com.example.robboss_mobile_app"
        minSdk = 34
        targetSdk = 37
        versionCode = 1
        versionName = "1.0"

        testInstrumentationRunner = "androidx.test.runner.AndroidJUnitRunner"

        buildConfigField("String", "GEMINI_API_KEY", javaStringLiteral(geminiKey))
        buildConfigField("String", "GEMINI_MODEL", javaStringLiteral(geminiModel))
        buildConfigField("String", "ROB_BOSS_API_URL", javaStringLiteral(apiUrl))
        buildConfigField("String", "ROB_BOSS_API_TOKEN", javaStringLiteral(apiToken))
    }

    buildTypes {
        release {
            optimization {
                enable = true
                packageScope = setOf("androidx.**", "kotlin.**", "kotlinx.**")
            }
        }
    }
    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_11
        targetCompatibility = JavaVersion.VERSION_11
    }
    buildFeatures {
        compose = true
        buildConfig = true
    }
}

dependencies {
    implementation(platform(libs.androidx.compose.bom))
    implementation(libs.androidx.activity.compose)
    implementation(libs.androidx.compose.material3)
    implementation(libs.androidx.compose.ui)
    implementation(libs.androidx.compose.ui.graphics)
    implementation(libs.androidx.compose.ui.tooling.preview)
    implementation(libs.androidx.core.ktx)
    implementation(libs.androidx.lifecycle.runtime.ktx)
    implementation(libs.androidx.lifecycle.viewmodel.compose)
    implementation(libs.kotlinx.serialization.json)
    implementation(libs.kotlinx.coroutines.android)
    implementation(libs.okhttp)
    testImplementation(libs.junit)
    testImplementation(libs.okhttp.mockwebserver)
    androidTestImplementation(platform(libs.androidx.compose.bom))
    androidTestImplementation(libs.androidx.compose.ui.test.junit4)
    androidTestImplementation(libs.androidx.espresso.core)
    androidTestImplementation(libs.androidx.junit)
    debugImplementation(libs.androidx.compose.ui.test.manifest)
    debugImplementation(libs.androidx.compose.ui.tooling)
}
