package com.example.robboss_mobile_app.ui

import android.Manifest
import android.content.Context
import android.content.pm.PackageManager
import android.net.Uri
import android.provider.OpenableColumns
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.PickVisualMediaRequest
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.runtime.Composable
import androidx.compose.runtime.remember
import androidx.compose.ui.platform.LocalContext
import androidx.core.content.ContextCompat
import androidx.core.content.FileProvider
import java.io.File

data class ImageCaptureActions(
    val pick: () -> Unit,
    val take: () -> Unit,
)

@Composable
fun rememberImageCapture(onImage: (bytes: ByteArray, name: String) -> Unit): ImageCaptureActions {
    val context = LocalContext.current
    val takePicture = rememberLauncherForActivityResult(ActivityResultContracts.TakePicture()) { ok ->
        val file = captureFile(context)
        if (ok && file.exists()) onImage(file.readBytes(), "canvas.jpg")
    }
    val pick = rememberLauncherForActivityResult(ActivityResultContracts.PickVisualMedia()) { uri ->
        if (uri == null) return@rememberLauncherForActivityResult
        val bytes = context.contentResolver.openInputStream(uri)?.use { it.readBytes() } ?: return@rememberLauncherForActivityResult
        onImage(bytes, displayName(context, uri))
    }
    val permission = rememberLauncherForActivityResult(ActivityResultContracts.RequestPermission()) { granted ->
        if (granted) takePicture.launch(captureUri(context))
    }
    return remember(takePicture, pick, permission) {
        ImageCaptureActions(
            pick = { pick.launch(PickVisualMediaRequest(ActivityResultContracts.PickVisualMedia.ImageOnly)) },
            take = {
                val granted = ContextCompat.checkSelfPermission(context, Manifest.permission.CAMERA) ==
                    PackageManager.PERMISSION_GRANTED
                if (granted) takePicture.launch(captureUri(context)) else permission.launch(Manifest.permission.CAMERA)
            },
        )
    }
}

private fun captureFile(context: Context): File =
    File(File(context.cacheDir, "captures").apply { mkdirs() }, "canvas.jpg")

private fun captureUri(context: Context): Uri =
    FileProvider.getUriForFile(context, "${context.packageName}.fileprovider", captureFile(context))

private fun displayName(context: Context, uri: Uri): String {
    context.contentResolver.query(uri, arrayOf(OpenableColumns.DISPLAY_NAME), null, null, null)?.use { cursor ->
        if (cursor.moveToFirst()) return cursor.getString(0) ?: "upload.jpg"
    }
    return "upload.jpg"
}
