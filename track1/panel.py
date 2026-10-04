"""
The control panel as a local web page (replaces the OpenCV control window).

    panel = Panel(port=8765).start()          # serves http://localhost:8765/
    name, data = panel.wait_upload()          # upload screen; None if the page quit
    panel.stages(STAGES); panel.stage(0, "running", "detail")       # progress screen
    panel.stage(0, "done", "detail", image=sheet_bgr)                 # ...with a picture under it
    panel.update(step=..., lesson=..., status=..., preview=img_bgr)  # lesson screen
    key = panel.key(timeout_s=0.05)           # "next" | "back" | "outline" | "skip" | "quit" | None

The page polls /state.json and shows one of three screens by `phase`: "upload" (pick or drop
an image, POST /upload), "working" (the pipeline's stages) and "lesson" (the step's preview
/preview.jpg, Track 2's lesson for it, the lesson list, and the last stage picture as a thumbnail).
Stage pictures are served as /stage/<i>.jpg. Buttons / keyboard presses come back
as POST /key. Any device on the same network can open it too (http://<this PC>:8765/) with
--panel-host 0.0.0.0.
"""

from __future__ import annotations

import json
import queue
import threading
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import unquote

import cv2
import numpy as np

KEYS = ("next", "back", "outline", "skip", "quit")
MAX_UPLOAD_BYTES = 25 * 1024 * 1024

# Rough display colours for the kit in track2/palette.py (swatches on the page only).
PIGMENT_RGB = {
    "titanium white": (245, 245, 240), "ivory black": (30, 30, 30),
    "ultramarine blue": (30, 50, 160), "cadmium yellow": (250, 200, 20),
    "cadmium red": (200, 30, 30), "burnt umber": (90, 55, 35),
}


class Panel:
    def __init__(self, host: str = "127.0.0.1", port: int = 8765):
        self.host, self.port = host, port
        self._lock = threading.Lock()
        self._state: dict = {"version": 0, "phase": "working", "stages": []}
        self._jpeg = b""
        self._stage_jpegs: dict[int, bytes] = {}
        self._keys: queue.Queue[str] = queue.Queue()
        self._uploads: queue.Queue[tuple[str, bytes]] = queue.Queue()
        self._server: ThreadingHTTPServer | None = None

    @property
    def url(self) -> str:
        return f"http://{'localhost' if self.host in ('127.0.0.1', '0.0.0.0') else self.host}:{self.port}/"

    def start(self) -> "Panel":
        panel = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_):          # keep the terminal for watcher events
                pass

            def _send(self, body: bytes, ctype: str, status=HTTPStatus.OK):
                self.send_response(status)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(body)

            def _json(self, value, status=HTTPStatus.OK):
                self._send(json.dumps(value).encode(), "application/json", status)

            def do_GET(self):
                path = self.path.split("?")[0]
                if path == "/":
                    self._send(PAGE.encode(), "text/html; charset=utf-8")
                elif path == "/state.json":
                    with panel._lock:
                        body = json.dumps(panel._state).encode()
                    self._send(body, "application/json")
                elif path.startswith("/stage/") and path.endswith(".jpg"):
                    with panel._lock:
                        body = panel._stage_jpegs.get(int(path[7:-4]) if path[7:-4].isdigit() else -1, b"")
                    self._send(body, "image/jpeg") if body else self._send(b"not found", "text/plain", HTTPStatus.NOT_FOUND)
                elif path == "/preview.jpg":
                    with panel._lock:
                        body = panel._jpeg
                    self._send(body, "image/jpeg") if body else self._send(b"", "text/plain", HTTPStatus.NO_CONTENT)
                else:
                    self._send(b"not found", "text/plain", HTTPStatus.NOT_FOUND)

            def do_POST(self):
                n = int(self.headers.get("Content-Length") or 0)
                if self.path == "/upload":
                    if panel._state.get("phase") != "upload":
                        return self._json({"ok": False, "error": "hang on, I'm not quite ready for a picture yet"}, HTTPStatus.CONFLICT)
                    if not 0 < n <= MAX_UPLOAD_BYTES:
                        return self._json({"ok": False, "error": "that picture's a little big - keep it under 25 MB"},
                                          HTTPStatus.REQUEST_ENTITY_TOO_LARGE)
                    data = self.rfile.read(n)
                    if cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR) is None:
                        return self._json({"ok": False, "error": "I can't quite read that picture - try a JPG or PNG"}, HTTPStatus.BAD_REQUEST)
                    panel._uploads.put((unquote(self.headers.get("X-Filename") or "upload.jpg"), data))
                    return self._json({"ok": True})
                if self.path != "/key":
                    return self._send(b"not found", "text/plain", HTTPStatus.NOT_FOUND)
                try:
                    key = json.loads(self.rfile.read(n) or b"{}").get("key")
                except (ValueError, AttributeError):
                    key = None
                if key not in KEYS:
                    return self._json({"ok": False}, HTTPStatus.BAD_REQUEST)
                panel._keys.put(key)
                self._json({"ok": True})

        self._server = ThreadingHTTPServer((self.host, self.port), Handler)
        self._server.daemon_threads = True
        threading.Thread(target=self._server.serve_forever, daemon=True).start()
        return self

    def _set(self, **fields) -> None:
        with self._lock:
            self._state.update(fields, version=self._state["version"] + 1)

    # ---- upload screen ---------------------------------------------------------------
    def wait_upload(self) -> tuple[str, bytes] | None:
        """Show the upload screen until an image arrives (filename, bytes); None if the page quit."""
        self._set(phase="upload")
        while True:
            try:
                return self._uploads.get(timeout=0.1)
            except queue.Empty:
                if self.key(0) == "quit":
                    return None

    # ---- progress screen -------------------------------------------------------------
    def stages(self, names: list[str]) -> None:
        self._set(phase="working", stages=[{"name": n, "state": "pending", "detail": ""} for n in names])

    def stage(self, i: int, state: str, detail: str = "", image: np.ndarray | None = None) -> None:
        """state: pending | running | done | skipped | failed. `image` (BGR) is shown under the stage."""
        jpeg = _jpeg(image, 1440) if image is not None else None
        with self._lock:
            self._state["stages"][i].update(state=state, detail=detail)
            if jpeg is not None:
                self._stage_jpegs[i] = jpeg
                self._state["stages"][i]["image"] = f"/stage/{i}.jpg?v={self._state['version'] + 1}"
            self._state["version"] += 1

    # ---- lesson screen ---------------------------------------------------------------
    def update(self, *, step: dict, index: int, count: int, outline: bool, lesson: dict | None = None,
               lessons: list[dict] | None = None, status: str | None = None, tone: str | None = None,
               painted: int | None = None, watching: bool = False, preview: np.ndarray | None = None) -> None:
        """Publish what is projected now. `preview` (BGR) is only re-encoded when passed.
        `tone`: "done" shows the status as good news. `painted`: % of the step's area painted, if measured."""
        jpeg = _jpeg(preview, 720) if preview is not None else None
        mix = [{"pigment": m["pigment"], "parts": m["parts"], "rgb": PIGMENT_RGB.get(m["pigment"])}
               for m in (lesson or {}).get("mix", [])]
        with self._lock:
            version = self._state["version"] + 1
            if jpeg is not None:
                self._jpeg = jpeg
                self._state["preview_version"] = version
            self._state.update(
                version=version, phase="lesson", index=index, count=count, outline=outline, watching=watching,
                status=status, tone=tone, painted=painted, layer=step["name"], lesson=lesson | {"mix": mix} if lesson else None,
                lessons=[{"index": l["index"], "name": l["name"]} for l in (lessons or [])],
            )

    def key(self, timeout_s: float = 0.05) -> str | None:
        try:
            return self._keys.get(timeout=timeout_s) if timeout_s > 0 else self._keys.get_nowait()
        except queue.Empty:
            return None

    def close(self) -> None:
        if self._server:
            self._server.shutdown()
            self._server.server_close()


def _jpeg(img: np.ndarray, longest: int) -> bytes:
    h, w = img.shape[:2]
    if max(h, w) > longest:
        img = cv2.resize(img, (round(w * longest / max(h, w)), round(h * longest / max(h, w))))
    return cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 88])[1].tobytes()


PAGE = r"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>RobBoss</title>
<style>
:root { --bg:#f6f4ef; --card:#fff; --ink:#1d1d1f; --muted:#6b6b70; --line:#e2ded6;
        --accent:#c2410c; --ok:#15803d; --warn:#b45309; --bad:#b91c1c; }
@media (prefers-color-scheme: dark) { :root { --bg:#141416; --card:#1e1e22; --ink:#f2f2f2;
        --muted:#9a9aa2; --line:#2e2e34; --accent:#fb923c; --ok:#4ade80; --warn:#fbbf24; --bad:#f87171; } }
* { box-sizing:border-box; }
body { margin:0; background:var(--bg); color:var(--ink); font:16px/1.45 system-ui, sans-serif; }
[hidden] { display:none !important; }
main { max-width:1200px; margin:0 auto; padding:16px; display:grid; gap:16px;
       grid-template-columns: minmax(0,1.1fr) minmax(0,1fr) 220px; }
@media (max-width: 1000px) { main { grid-template-columns: 1fr; } }
.narrow { max-width:640px; margin:0 auto; padding:16px; }
.card { background:var(--card); border:1px solid var(--line); border-radius:12px; padding:16px; }
h1 { font-size:26px; margin:2px 0 10px; }
.kicker { color:var(--muted); font-size:13px; text-transform:uppercase; letter-spacing:.06em; }
img { width:100%; border-radius:8px; display:block; background:#000; }
.drop { border:2px dashed var(--line); border-radius:12px; padding:32px 16px; text-align:center;
        cursor:pointer; color:var(--muted); margin:12px 0; }
.drop.over { border-color:var(--accent); color:var(--ink); }
.drop img { max-height:320px; object-fit:contain; background:transparent; margin:0 auto 10px; }
.error { color:var(--bad); font-weight:600; margin-top:8px; }
.stages { list-style:none; padding:0; margin:12px 0 0; }
.stages li { display:flex; gap:12px; padding:10px 0; border-top:1px solid var(--line); color:var(--ink); }
.stages li:first-child { border-top:0; }
.icon { width:22px; flex:none; text-align:center; font-weight:700; }
.stages .pending { color:var(--muted); }
.stages .done .icon { color:var(--ok); } .stages .failed .icon { color:var(--bad); }
.stages .running .icon { color:var(--accent); animation:pulse 1s infinite alternate; }
@keyframes pulse { from { opacity:.3 } to { opacity:1 } }
.detail { color:var(--muted); font-size:14px; }
.mix { display:flex; flex-wrap:wrap; gap:8px; margin:8px 0 14px; }
.chip { display:flex; align-items:center; gap:8px; border:1px solid var(--line); border-radius:999px;
        padding:4px 12px 4px 4px; font-size:15px; }
.dot { width:26px; height:26px; border-radius:50%; border:1px solid rgba(0,0,0,.2); flex:none; }
.target { display:flex; align-items:center; gap:10px; color:var(--muted); font-size:14px; }
.target .dot { width:36px; height:36px; border-radius:8px; }
.label { font-weight:600; margin-top:12px; }
.status { margin-top:14px; padding:12px; border-radius:8px; border-left:4px solid var(--warn);
          background:color-mix(in srgb, var(--warn) 12%, transparent); font-weight:600; }
.status.done { border-color:var(--ok); background:color-mix(in srgb, var(--ok) 12%, transparent); }
.progress { margin-top:14px; font-size:14px; color:var(--muted); }
.bar { height:8px; border-radius:999px; background:var(--line); overflow:hidden; margin-top:6px; }
.bar > div { height:100%; background:var(--ok); transition:width .4s; }
.buttons { display:flex; flex-wrap:wrap; gap:8px; margin-top:16px; }
button { font:inherit; padding:10px 16px; border-radius:8px; border:1px solid var(--line);
         background:var(--card); color:var(--ink); cursor:pointer; }
button.primary { background:var(--accent); border-color:var(--accent); color:#fff; font-weight:600; }
button:disabled { opacity:.5; cursor:default; }
ol { margin:0; padding-left:22px; }
#steps li { padding:3px 0; color:var(--muted); }
#steps li.done { text-decoration:line-through; }
#steps li.now { color:var(--ink); font-weight:700; }
.offline { color:var(--warn); font-weight:600; }
kbd { border:1px solid var(--line); border-radius:4px; padding:0 5px; font-size:12px; }
.hint { color:var(--muted); font-size:13px; margin-top:10px; }
.stage-img { margin-top:8px; max-width:100%; }
#work-view { max-width:960px; }
#sheet-link { display:block; margin-top:14px; }
#sheet-link img { background:transparent; }
</style></head>
<body>
<div id="upload-view" class="narrow" hidden><section class="card">
  <div class="kicker">RobBoss</div><h1>What would you like to paint today?</h1>
  <label class="drop" id="drop">
    <img id="upload-preview" alt="" hidden>
    <div id="drop-text">Drop a picture right here, or click to pick one (JPG or PNG)</div>
    <input type="file" id="file" accept="image/*" hidden>
  </label>
  <div class="buttons"><button id="start" class="primary" disabled>Let's paint</button>
    <button data-key="quit">Quit</button></div>
  <div id="upload-error" class="error" hidden></div>
</section></div>

<div id="work-view" class="narrow" hidden><section class="card">
  <div class="kicker">Getting ready</div><h1>Let's get our paints ready</h1>
  <ul class="stages" id="stages"></ul>
  <div class="buttons"><button data-key="quit">Cancel</button></div>
</section></div>

<main id="lesson-view" hidden>
  <section class="card"><div class="kicker">On your canvas now</div>
    <img id="preview" alt="Projected step"></section>
  <section class="card">
    <div class="kicker" id="count">Step</div>
    <h1 id="name"></h1>
    <div id="lesson"></div>
    <div id="progress" class="progress" hidden><span id="progress-text"></span>
      <div class="bar"><div id="progress-bar"></div></div></div>
    <div id="status" class="status" hidden></div>
    <div class="buttons">
      <button id="back" data-key="back">← Back</button>
      <button id="next" class="primary" data-key="next">Next →</button>
      <button id="skip" data-key="skip" hidden>Let this one be</button>
      <button id="outline" data-key="outline">Outline</button>
      <button data-key="quit">Quit</button>
    </div>
    <div class="hint" id="hint"></div>
  </section>
  <aside class="card"><div class="kicker">Today's lesson</div><ol id="steps"></ol>
    <a id="sheet-link" target="_blank" hidden><div class="kicker">All the happy little layers</div><img id="sheet" alt="Every step"></a></aside>
</main>
<div id="gone" class="narrow" hidden><section class="card"><h1>That's all for today</h1>
  <p class="detail">The show command has stopped. Run it again whenever you're ready to paint some more.</p></section></div>
<script>
const $ = id => document.getElementById(id);
const esc = s => String(s ?? "").replace(/[&<>"]/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c]));
let version = -1, previewVersion = -1, phase = null, chosen = null;

function send(key) {
  fetch("/key", {method:"POST", headers:{"Content-Type":"application/json"}, body:JSON.stringify({key})})
    .then(poll).catch(() => {});
}
document.querySelectorAll("button[data-key]").forEach(b => b.onclick = () => send(b.dataset.key));
document.addEventListener("keydown", e => {
  if (phase !== "lesson") { if (e.key === "Escape") send("quit"); return; }
  const k = {" ":"next","ArrowRight":"next","n":"next","N":"next","ArrowLeft":"back","b":"back","B":"back",
             "o":"outline","O":"outline","k":"skip","K":"skip","Escape":"quit","q":"quit","Q":"quit"}[e.key];
  if (k) { e.preventDefault(); send(k); }
});

// ---- upload screen
function choose(file) {
  if (!file || !file.type.startsWith("image/")) return showError("Hmm, that doesn't look like a picture. Try a JPG or PNG.");
  chosen = file; $("upload-error").hidden = true;
  $("upload-preview").src = URL.createObjectURL(file); $("upload-preview").hidden = false;
  $("drop-text").textContent = file.name + " - click to pick a different one";
  $("start").disabled = false;
}
function showError(msg) { $("upload-error").textContent = msg; $("upload-error").hidden = false; }
$("file").onchange = e => choose(e.target.files[0]);
const drop = $("drop");
drop.ondragover = e => { e.preventDefault(); drop.classList.add("over"); };
drop.ondragleave = () => drop.classList.remove("over");
drop.ondrop = e => { e.preventDefault(); drop.classList.remove("over"); choose(e.dataTransfer.files[0]); };
$("start").onclick = () => {
  if (!chosen) return;
  $("start").disabled = true; $("start").textContent = "Sending it over…";
  fetch("/upload", {method:"POST", headers:{"Content-Type":chosen.type, "X-Filename":encodeURIComponent(chosen.name)},
                    body:chosen})
    .then(r => r.json()).then(j => { if (!j.ok) throw new Error(j.error); poll(); })
    .catch(err => { showError("That one didn't quite make it: " + err.message); $("start").disabled = false; })
    .finally(() => { $("start").textContent = "Let's paint"; });
};

// ---- progress screen
const ICON = {pending:"○", running:"●", done:"✓", skipped:"–", failed:"✕"};
function renderStages(s) {
  $("stages").innerHTML = s.stages.map(g =>
    `<li class="${g.state}"><span class="icon">${ICON[g.state] || ""}</span><div><div>${esc(g.name)}</div>` +
    (g.detail ? `<div class="detail">${esc(g.detail)}</div>` : "") +
    (g.image ? `<img class="stage-img" src="${esc(g.image)}" alt="">` : "") + `</div></li>`).join("");
}

// ---- lesson screen
function renderLesson(s) {
  $("count").textContent = `Step ${s.index} of ${s.count}` + (s.watching ? " · I'm watching" : "");
  const L = s.lesson;
  $("name").textContent = L ? L.name : s.layer;
  if (L) {
    const rgb = L.target_rgb ? `rgb(${L.target_rgb.join(",")})` : "transparent";
    $("lesson").innerHTML =
      `<div class="label">Let's mix up</div><div class="mix">` +
      L.mix.map(m => `<span class="chip"><span class="dot" style="background:${m.rgb ? `rgb(${m.rgb.join(",")})` : "#888"}"></span>${m.parts} × ${esc(m.pigment)}</span>`).join("") +
      `</div><div class="target"><span class="dot" style="background:${rgb}"></span>the colour we're after · brush: <b>${esc(L.brush)}</b></div>` +
      `<div class="label">How we'll do it</div><div>${esc(L.technique)}</div>` +
      `<div class="label">You'll know it's done when</div><div>${esc(L.success)}</div>`;
  } else {
    $("lesson").innerHTML = `<p class="offline">We don't have a lesson for this one yet. Run: uv run python -m track2.demo plan IMAGE --out SCENE</p>`;
  }
  $("status").hidden = !s.status;
  $("status").textContent = s.status || "";
  $("status").classList.toggle("done", s.tone === "done");
  $("next").textContent = s.watching ? "I'm happy with it →" : "Next →";
  $("back").hidden = !!s.watching;
  $("skip").hidden = !s.watching;
  $("progress").hidden = !(s.watching && s.painted != null);
  if (s.painted != null) {
    $("progress-text").textContent = `About ${s.painted}% of this area painted so far`;
    $("progress-bar").style.width = `${Math.min(100, s.painted)}%`;
  }
  $("outline").textContent = `Outline: ${s.outline ? "on" : "off"}`;
  $("hint").innerHTML = s.watching
    ? `I'll take a peek about 2 s after your brush leaves the area · <kbd>Space</kbd> I'm happy with it · <kbd>K</kbd> let it be · <kbd>O</kbd> outline · <kbd>Esc</kbd> quit`
    : `<kbd>Space</kbd>/<kbd>→</kbd> next · <kbd>←</kbd> back · <kbd>O</kbd> outline · <kbd>Esc</kbd> quit`;
  $("steps").innerHTML = (s.lessons || []).map(l =>
    `<li class="${l.index < s.index ? "done" : l.index === s.index ? "now" : ""}">${esc(l.name)}</li>`).join("");
  const sheet = (s.stages || []).map(g => g.image).filter(Boolean).pop();
  $("sheet-link").hidden = !sheet;
  if (sheet && $("sheet-link").getAttribute("href") !== sheet) { $("sheet-link").href = sheet; $("sheet").src = sheet; }
  if (s.preview_version !== previewVersion) {
    previewVersion = s.preview_version;
    $("preview").src = `/preview.jpg?v=${previewVersion}`;
  }
}

function show(view) {
  for (const v of ["upload-view", "work-view", "lesson-view", "gone"]) $(v).hidden = v !== view;
}
function poll() {
  return fetch("/state.json", {cache:"no-store"}).then(r => r.json()).then(s => {
    if (s.version === version) return;
    version = s.version; phase = s.phase;
    if (s.phase === "upload") show("upload-view");
    else if (s.phase === "working") { show("work-view"); renderStages(s); }
    else if (s.phase === "lesson") { show("lesson-view"); renderLesson(s); }
  }).catch(() => { show("gone"); version = -1; });
}
setInterval(poll, 300); poll();
</script></body></html>
"""
