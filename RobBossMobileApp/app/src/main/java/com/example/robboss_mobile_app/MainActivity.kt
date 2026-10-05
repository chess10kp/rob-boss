package com.example.robboss_mobile_app

import android.app.Activity
import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.activity.enableEdgeToEdge
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.WindowInsets
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.safeDrawing
import androidx.compose.foundation.layout.widthIn
import androidx.compose.foundation.layout.windowInsetsPadding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.unit.dp
import androidx.lifecycle.viewmodel.compose.viewModel
import com.example.robboss_mobile_app.domain.LessonViewModel
import com.example.robboss_mobile_app.domain.Phase
import com.example.robboss_mobile_app.ui.DoneScreen
import com.example.robboss_mobile_app.ui.LessonScreen
import com.example.robboss_mobile_app.ui.UploadScreen
import com.example.robboss_mobile_app.ui.WorkingScreen
import com.example.robboss_mobile_app.ui.rememberImageCapture
import com.example.robboss_mobile_app.ui.theme.LocalRobBossColors
import com.example.robboss_mobile_app.ui.theme.RobBossmobileappTheme

class MainActivity : ComponentActivity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        enableEdgeToEdge()
        setContent {
            RobBossmobileappTheme {
                val viewModel: LessonViewModel = viewModel()
                val state by viewModel.state.collectAsState()
                val activity = LocalContext.current as? Activity
                val checkCapture = rememberImageCapture { bytes, _ -> viewModel.check(bytes) }
                val colors = LocalRobBossColors.current
                val wide = state.phase == Phase.Lesson
                Box(
                    Modifier
                        .fillMaxSize()
                        .background(colors.bg)
                        .windowInsetsPadding(WindowInsets.safeDrawing)
                        .verticalScroll(rememberScrollState())
                        .padding(16.dp),
                    contentAlignment = Alignment.TopCenter,
                ) {
                    Box(Modifier.widthIn(max = if (wide) 720.dp else 640.dp)) {
                        when (state.phase) {
                            Phase.Upload -> UploadScreen(onStart = viewModel::start)
                            Phase.Working -> WorkingScreen(state.stages) { activity?.finish() }
                            Phase.Lesson -> LessonScreen(
                                state = state,
                                onBack = viewModel::back,
                                onNext = viewModel::next,
                                onOutline = viewModel::toggleOutline,
                                onCheck = checkCapture.take,
                                onQuit = { activity?.finish() },
                            )
                            Phase.Done -> DoneScreen(
                                message = state.status,
                                onAgain = viewModel::reset,
                                onQuit = { activity?.finish() },
                            )
                        }
                    }
                }
            }
        }
    }
}
