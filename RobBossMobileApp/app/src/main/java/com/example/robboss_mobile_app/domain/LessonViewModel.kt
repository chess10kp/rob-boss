package com.example.robboss_mobile_app.domain

import android.app.Application
import android.graphics.Bitmap
import androidx.lifecycle.AndroidViewModel
import androidx.lifecycle.viewModelScope
import com.example.robboss_mobile_app.BuildConfig
import com.example.robboss_mobile_app.data.GeminiClient
import com.example.robboss_mobile_app.data.GimpApi
import com.example.robboss_mobile_app.data.decodeSampled
import com.example.robboss_mobile_app.data.longHttpClient
import com.example.robboss_mobile_app.data.toJpeg
import com.example.robboss_mobile_app.model.Step
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch
import java.io.File

enum class Phase { Upload, Working, Lesson, Done }

data class StageUi(val name: String, val state: String = "pending", val detail: String = "")

data class UiState(
    val phase: Phase = Phase.Upload,
    val uploadError: String? = null,
    val stages: List<StageUi> = emptyList(),
    val steps: List<Step> = emptyList(),
    val position: Int = 0,
    val outline: Boolean = true,
    val status: String? = null,
    val tone: String? = null,
    val checking: Boolean = false,
    val busy: Boolean = false,
    val reference: Bitmap? = null,
    val masks: Map<String, Bitmap> = emptyMap(),
)

class LessonViewModel(app: Application) : AndroidViewModel(app) {
    private val http = longHttpClient()
    private val gemini = GeminiClient(http, BuildConfig.GEMINI_API_KEY, BuildConfig.GEMINI_MODEL)
    private val gimp = GimpApi(http, BuildConfig.ROB_BOSS_API_URL, BuildConfig.ROB_BOSS_API_TOKEN)
    private val critic = Critic(gemini)
    private val pipeline = PrepareLesson(
        gimp = gimp,
        planner = Planner(gemini),
        cacheRoot = File(app.cacheDir, "jobs"),
        geminiKey = BuildConfig.GEMINI_API_KEY,
    )

    private val _state = MutableStateFlow(UiState())
    val state: StateFlow<UiState> = _state.asStateFlow()
    private var hold: PreparedLesson? = null

    fun start(bytes: ByteArray, filename: String) {
        if (_state.value.busy) return
        viewModelScope.launch(Dispatchers.IO) {
            _state.update {
                it.copy(busy = true, phase = Phase.Working, uploadError = null, stages = freshStages(), status = null)
            }
            try {
                val ready = pipeline.run(bytes, filename) { index, stageState, detail ->
                    _state.update { current ->
                        val stages = current.stages.toMutableList()
                        stages[index] = stages[index].copy(state = stageState, detail = detail)
                        current.copy(stages = stages)
                    }
                }
                hold = ready
                _state.update {
                    it.copy(
                        busy = false,
                        phase = Phase.Lesson,
                        steps = ready.steps,
                        position = 0,
                        reference = ready.reference,
                        masks = ready.masks,
                        status = null,
                        tone = null,
                        outline = true,
                    )
                }
            } catch (error: Exception) {
                _state.update { current ->
                    val stages = current.stages.toMutableList()
                    val index = stages.indexOfFirst { stage -> stage.state == "running" }
                        .let { found -> if (found < 0) stages.lastIndex else found }
                    if (index >= 0) {
                        stages[index] = stages[index].copy(state = "failed", detail = error.message ?: "something went wrong")
                    }
                    current.copy(stages = stages, busy = false)
                }
            }
        }
    }

    fun check(photo: ByteArray) {
        val lesson = hold ?: return
        val current = _state.value
        val step = current.steps.getOrNull(current.position) ?: return
        if (current.checking || current.phase != Phase.Lesson) return
        viewModelScope.launch(Dispatchers.IO) {
            _state.update { it.copy(checking = true, status = "Taking a look…", tone = null) }
            try {
                val photoJpeg = decodeSampled(photo, 1024).toJpeg(85)
                val mask = lesson.maskPng[step.maskId] ?: error("This step has no mask.")
                val verdict = critic.critique(lesson.refJpeg, photoJpeg, mask, step)
                _state.update { state ->
                    if (verdict.verdict == "READY") {
                        if (state.position >= state.steps.lastIndex) {
                            state.copy(checking = false, phase = Phase.Done, status = COMPLETE, tone = "done")
                        } else {
                            state.copy(checking = false, position = state.position + 1, status = ADVANCED, tone = "done")
                        }
                    } else {
                        state.copy(checking = false, status = verdict.adjustment, tone = null)
                    }
                }
            } catch (error: Exception) {
                _state.update {
                    it.copy(checking = false, status = error.message ?: "The critique didn't come back.", tone = null)
                }
            }
        }
    }

    fun next() {
        _state.update { state ->
            if (state.phase != Phase.Lesson) state
            else if (state.position >= state.steps.lastIndex) state.copy(phase = Phase.Done, status = COMPLETE, tone = "done")
            else state.copy(position = state.position + 1, status = null, tone = null)
        }
    }

    fun back() {
        _state.update { state ->
            if (state.phase == Phase.Lesson && state.position > 0) {
                state.copy(position = state.position - 1, status = null, tone = null)
            } else {
                state
            }
        }
    }

    fun toggleOutline() {
        _state.update { it.copy(outline = !it.outline) }
    }

    fun reset() {
        hold = null
        _state.value = UiState()
    }

    private fun freshStages() = STAGE_NAMES.map { StageUi(it) }

    private companion object {
        val STAGE_NAMES = listOf(
            "Contacting server",
            "GIMP layers",
            "Downloading masks",
            "Planning lesson",
        )
        const val ADVANCED = "Beautiful. That one's finished - let's move right along."
        const val COMPLETE = "And there you have it. Your painting's finished - happy painting, friend."
    }
}
