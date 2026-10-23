package ch.nrmm.app

import android.annotation.SuppressLint
import android.content.Intent
import android.net.Uri
import android.os.Bundle
import android.webkit.JavascriptInterface
import android.webkit.WebChromeClient
import android.webkit.WebView
import android.webkit.WebViewClient
import android.widget.Toast
import androidx.activity.result.contract.ActivityResultContracts
import androidx.appcompat.app.AppCompatActivity
import androidx.core.content.FileProvider
import com.chaquo.python.Python
import com.chaquo.python.android.AndroidPlatform
import org.json.JSONObject
import java.io.File
import java.util.zip.ZipEntry
import java.util.zip.ZipOutputStream

class MainActivity : AppCompatActivity() {
    private lateinit var web: WebView
    private var pendingKind = "pbf"

    private val picker = registerForActivityResult(ActivityResultContracts.OpenDocument()) { uri ->
        if (uri == null) { callback(null); return@registerForActivityResult }
        Thread {
            try {
                val dir = File(filesDir, "import").apply { mkdirs() }
                var name = Uri.decode(uri.lastPathSegment ?: "datei").substringAfterLast('/').substringAfterLast(':')
                if (name.isBlank()) name = if (pendingKind == "gpx") "track.gpx" else "karte.osm.pbf"
                val out = File(dir, name)
                contentResolver.openInputStream(uri)!!.use { i -> out.outputStream().use { o -> i.copyTo(o, 1 shl 20) } }
                callback(out.absolutePath)
            } catch (e: Exception) {
                runOnUiThread { Toast.makeText(this, "Datei konnte nicht gelesen werden: ${e.message}", Toast.LENGTH_LONG).show() }
                callback(null)
            }
        }.start()
    }

    private fun callback(path: String?) {
        val arg = if (path == null) "null" else JSONObject.quote(path)
        runOnUiThread { web.evaluateJavascript("window.__pickDone && window.__pickDone($arg)", null) }
    }

    inner class Bridge {
        @JavascriptInterface
        fun pick(kind: String) {
            pendingKind = kind
            if (kind == "folder") { callback(outDir().absolutePath); return }
            runOnUiThread { picker.launch(arrayOf("*/*")) }
        }

        @JavascriptInterface
        fun share(dir: String) {
            try {
                val src = File(dir)
                val files = src.listFiles()?.filter { it.isFile }?.sortedByDescending { it.lastModified() }?.take(6).orEmpty()
                if (files.isEmpty()) return
                val zipDir = File(cacheDir, "share").apply { mkdirs() }
                val zip = File(zipDir, "route.zip")
                ZipOutputStream(zip.outputStream()).use { z ->
                    for (f in files) { z.putNextEntry(ZipEntry(f.name)); f.inputStream().use { it.copyTo(z) }; z.closeEntry() }
                }
                val uri = FileProvider.getUriForFile(this@MainActivity, "ch.nrmm.app.files", zip)
                val i = Intent(Intent.ACTION_SEND).apply {
                    type = "application/zip"; putExtra(Intent.EXTRA_STREAM, uri)
                    addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION)
                }
                startActivity(Intent.createChooser(i, "Route speichern oder teilen"))
            } catch (e: Exception) {
                runOnUiThread { Toast.makeText(this@MainActivity, "Teilen fehlgeschlagen: ${e.message}", Toast.LENGTH_LONG).show() }
            }
        }
    }

    private fun outDir() = File(getExternalFilesDir(null) ?: filesDir, "Routen").apply { mkdirs() }

    @SuppressLint("SetJavaScriptEnabled")
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        if (!Python.isStarted()) Python.start(AndroidPlatform(this))
        val port = Python.getInstance().getModule("nrmm.android_entry")
            .callAttr("start", File(filesDir, "nrmm").absolutePath, outDir().absolutePath).toInt()

        web = WebView(this).apply {
            settings.javaScriptEnabled = true
            settings.domStorageEnabled = true
            settings.useWideViewPort = true
            settings.builtInZoomControls = false
            webChromeClient = WebChromeClient()
            webViewClient = WebViewClient()
            addJavascriptInterface(Bridge(), "AndroidBridge")
        }
        setContentView(web)
        web.loadUrl("http://127.0.0.1:$port/")
    }

    @Deprecated("Deprecated in Java")
    override fun onBackPressed() { moveTaskToBack(true) }
}
