package com.example.robboss_mobile_app.ui

import android.graphics.BitmapFactory
import androidx.compose.foundation.Image
import com.example.robboss_mobile_app.data.decodeSampled
import androidx.compose.foundation.border
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.asImageBitmap
import androidx.compose.ui.layout.ContentScale
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.dp
import com.example.robboss_mobile_app.ui.theme.LocalRobBossColors

@Composable
fun UploadScreen(onStart: (ByteArray, String) -> Unit) {
    val colors = LocalRobBossColors.current
    var chosen by remember { mutableStateOf<ByteArray?>(null) }
    var name by remember { mutableStateOf("") }
    var error by remember { mutableStateOf<String?>(null) }
    val capture = rememberImageCapture { bytes, filename ->
        if (BitmapFactory.decodeByteArray(bytes, 0, bytes.size) == null) {
            error = "Hmm, that doesn't look like a picture. Try a JPG or PNG."
            chosen = null
        } else {
            error = null
            chosen = bytes
            name = filename
        }
    }
    val preview = remember(chosen) {
        chosen?.let { runCatching { decodeSampled(it, 1024).asImageBitmap() }.getOrNull() }
    }
    BossCard {
        Kicker("RobBoss")
        Text("What would you like to paint today?", style = MaterialTheme.typography.titleLarge)
        Column(
            Modifier
                .padding(top = 12.dp)
                .fillMaxWidth()
                .border(2.dp, colors.line, RoundedCornerShape(12.dp))
                .padding(16.dp),
            verticalArrangement = Arrangement.spacedBy(8.dp),
        ) {
            if (preview != null) {
                Image(
                    bitmap = preview,
                    contentDescription = name,
                    modifier = Modifier.fillMaxWidth().heightIn(max = 320.dp),
                    contentScale = ContentScale.Fit,
                )
            }
            Text(
                if (name.isBlank()) "Pick a picture, or take one with the camera (JPG or PNG)" else name,
                color = colors.muted,
                textAlign = TextAlign.Center,
                modifier = Modifier.fillMaxWidth(),
            )
        }
        Row(Modifier.padding(top = 16.dp), horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            BossButton("Choose picture", capture.pick)
            BossButton("Camera", capture.take)
        }
        BossButton(
            "Let's paint",
            onClick = { chosen?.let { onStart(it, name.ifBlank { "upload.jpg" }) } },
            primary = true,
            enabled = chosen != null,
            modifier = Modifier.padding(top = 8.dp).fillMaxWidth(),
        )
        if (error != null) {
            Text(error!!, color = colors.bad, fontWeight = androidx.compose.ui.text.font.FontWeight.SemiBold, modifier = Modifier.padding(top = 8.dp))
        }
    }
}
