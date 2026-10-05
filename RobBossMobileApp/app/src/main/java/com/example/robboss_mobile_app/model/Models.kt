package com.example.robboss_mobile_app.model

import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable
import kotlinx.serialization.json.Json

val AppJson = Json { ignoreUnknownKeys = true }

@Serializable
data class MixPart(val pigment: String, val parts: Int)

@Serializable
data class MixDraft(val pigment: String, val parts: Int)

@Serializable
data class StepDraft(
    @SerialName("mask_id") val maskId: String,
    val name: String,
    val mix: List<MixDraft>,
    val brush: String,
    val technique: String,
    @SerialName("stroke_dir_deg") val strokeDirDeg: Int,
    val success: String,
)

@Serializable
data class PlanDraft(val steps: List<StepDraft>)

data class Step(
    val index: Int,
    val name: String,
    val maskPath: String,
    val maskId: String,
    val targetRgb: List<Int>,
    val mix: List<MixPart>,
    val brush: String,
    val technique: String,
    val strokeDirDeg: Int,
    val success: String,
)

@Serializable
data class Verdict(
    val verdict: String,
    val category: String,
    val adjustment: String,
)

@Serializable
data class GimpStep(
    val path: String,
    @SerialName("target_rgb") val targetRgb: List<Int> = emptyList(),
    val coverage: Double = 0.0,
    val name: String = "",
)

@Serializable
data class GimpArtifacts(
    val manifest: String = "",
    val posterized: String = "",
    @SerialName("contact_sheet") val contactSheet: String = "",
)

@Serializable
data class GimpJob(
    @SerialName("job_id") val jobId: String,
    val engine: String = "",
    val width: Int = 0,
    val height: Int = 0,
    val steps: List<GimpStep> = emptyList(),
    val artifacts: GimpArtifacts = GimpArtifacts(),
)
