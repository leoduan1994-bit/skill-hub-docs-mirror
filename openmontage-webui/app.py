#!/usr/bin/env python3
"""OpenMontage Render Control Panel — a small web UI for the zero-key demos.

Drop this folder at the root of an OpenMontage checkout (so the layout is
`OpenMontage/openmontage-webui/app.py`) and run:

    pip install flask
    python openmontage-webui/app.py

Then open http://localhost:8000

What it does:
  * Lists the checked-in Remotion demos (the same ones `render_demo.py` ships).
  * Lets you optionally edit a demo's props JSON before rendering.
  * Renders via `npx remotion render ... --codec h264`, streaming the log.
  * Previews and downloads the resulting MP4 in the browser.

No API keys are required — these demos render from local Remotion components.
The first render may download a headless-Chrome shell via npx (one-time).
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import threading
import time
import uuid
from pathlib import Path

from flask import (
    Flask,
    Response,
    abort,
    jsonify,
    request,
    send_file,
)

# --------------------------------------------------------------------------- #
# Locate the OpenMontage checkout this UI is operating on.
# --------------------------------------------------------------------------- #


def _find_root() -> Path:
    """Find the OpenMontage root (the dir that holds `remotion-composer/`)."""
    env = os.environ.get("OPENMONTAGE_ROOT")
    if env:
        return Path(env).expanduser().resolve()
    here = Path(__file__).resolve().parent
    for candidate in (here.parent, *here.parents):
        if (candidate / "remotion-composer").is_dir():
            return candidate
    # Fall back to the parent of this folder; errors surface clearly later.
    return here.parent


ROOT_DIR = _find_root()
COMPOSER_DIR = ROOT_DIR / "remotion-composer"
PROPS_DIR = COMPOSER_DIR / "public" / "demo-props"
OUTPUT_DIR = ROOT_DIR / "projects" / "demos" / "renders"
EDITED_PROPS_DIR = OUTPUT_DIR / "_webui_props"

DEMO_DESCRIPTIONS = {
    "world-in-numbers": "Global scale story with titles, stats, and charts",
    "code-to-screen": "Developer workflow explainer with comparison and KPI cards",
    "focusflow-pitch": "Startup-style pitch built only from Remotion components",
}

# --------------------------------------------------------------------------- #
# Job tracking — one render at a time (Remotion renders are heavy).
# --------------------------------------------------------------------------- #

_jobs: dict[str, dict] = {}
_jobs_lock = threading.Lock()
_active_job: str | None = None

_PROGRESS_RE = re.compile(r"(\d+)\s*/\s*(\d+)")


def discover_demos() -> dict[str, Path]:
    if not PROPS_DIR.exists():
        return {}
    return {p.stem: p for p in sorted(PROPS_DIR.glob("*.json"))}


def _which(*names: str) -> str | None:
    for name in names:
        found = shutil.which(name)
        if found:
            return found
    return None


def _run_render(job_id: str, demo: str, props_path: Path) -> None:
    """Worker thread: run the Remotion render and stream output into the job."""
    global _active_job
    job = _jobs[job_id]
    npx = _which("npx.cmd", "npx", "npx.exe")
    if not npx:
        job["status"] = "error"
        job["error"] = "npx not found on PATH (install Node.js 18+)."
        _active_job_release(job_id)
        return

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    output_path = OUTPUT_DIR / f"{demo}.mp4"
    job["output_name"] = output_path.name

    cmd = [
        npx, "remotion", "render", "src/index.tsx", "Explainer",
        str(output_path), "--props", str(props_path), "--codec", "h264",
    ]
    # Optional: reuse an already-installed Chrome instead of letting Remotion
    # download its headless-Chrome shell (handy offline or behind egress policy).
    browser_exe = os.environ.get("REMOTION_BROWSER_EXECUTABLE")
    if browser_exe:
        cmd += ["--browser-executable", browser_exe]
    job["log"].append(f"$ {' '.join(cmd)}")

    try:
        proc = subprocess.Popen(
            cmd,
            cwd=COMPOSER_DIR,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )
        job["_proc"] = proc
        for line in proc.stdout:  # type: ignore[union-attr]
            line = line.rstrip("\n")
            if not line:
                continue
            job["log"].append(line)
            del job["log"][:-400]  # keep the log bounded
            m = _PROGRESS_RE.search(line)
            if m:
                done, total = int(m.group(1)), int(m.group(2))
                if total:
                    job["progress"] = max(job["progress"], min(99, int(done / total * 100)))
        proc.wait()
        job["returncode"] = proc.returncode
        if proc.returncode == 0 and output_path.exists():
            job["status"] = "done"
            job["progress"] = 100
            job["output_url"] = f"/video/{output_path.name}"
            job["size_mb"] = round(output_path.stat().st_size / (1024 * 1024), 1)
        else:
            job["status"] = "error"
            job["error"] = f"Render exited with code {proc.returncode}."
    except Exception as exc:  # noqa: BLE001 - surface any failure to the UI
        job["status"] = "error"
        job["error"] = str(exc)
        job["log"].append(f"[exception] {exc}")
    finally:
        job.pop("_proc", None)
        _active_job_release(job_id)


def _active_job_release(job_id: str) -> None:
    global _active_job
    with _jobs_lock:
        if _active_job == job_id:
            _active_job = None


# --------------------------------------------------------------------------- #
# Flask app
# --------------------------------------------------------------------------- #

app = Flask(__name__)


@app.get("/")
def index() -> Response:
    return Response(INDEX_HTML, mimetype="text/html")


@app.get("/api/demos")
def api_demos():
    demos = discover_demos()
    if not demos:
        return jsonify(
            error=f"No demo props found in {PROPS_DIR}. Is this an OpenMontage checkout?",
            root=str(ROOT_DIR),
            demos=[],
        ), 200
    out = [
        {
            "name": name,
            "description": DEMO_DESCRIPTIONS.get(name, "Checked-in Remotion demo"),
            "rendered": (OUTPUT_DIR / f"{name}.mp4").exists(),
        }
        for name in demos
    ]
    return jsonify(root=str(ROOT_DIR), demos=out)


@app.get("/api/props/<demo>")
def api_props(demo: str):
    demos = discover_demos()
    if demo not in demos:
        abort(404)
    return Response(demos[demo].read_text(encoding="utf-8"), mimetype="application/json")


@app.post("/api/render")
def api_render():
    global _active_job
    payload = request.get_json(silent=True) or {}
    demo = payload.get("demo")
    demos = discover_demos()
    if demo not in demos:
        return jsonify(error=f"Unknown demo '{demo}'."), 400

    # Resolve the props file: default checked-in, or user-edited JSON.
    props_path = demos[demo]
    edited = payload.get("props")
    if edited is not None and edited.strip():
        try:
            parsed = json.loads(edited)
        except json.JSONDecodeError as exc:
            return jsonify(error=f"Props is not valid JSON: {exc}"), 400
        if not isinstance(parsed.get("cuts"), list) or not parsed["cuts"]:
            return jsonify(error="Props must define a non-empty 'cuts' array."), 400
        EDITED_PROPS_DIR.mkdir(parents=True, exist_ok=True)
        props_path = EDITED_PROPS_DIR / f"{demo}.json"
        props_path.write_text(json.dumps(parsed, indent=2), encoding="utf-8")

    with _jobs_lock:
        if _active_job is not None:
            return jsonify(error="A render is already in progress. Please wait."), 409
        job_id = uuid.uuid4().hex[:12]
        _active_job = job_id
        _jobs[job_id] = {
            "id": job_id,
            "demo": demo,
            "status": "running",
            "progress": 0,
            "log": [],
            "started": time.time(),
            "output_url": None,
        }

    threading.Thread(
        target=_run_render, args=(job_id, demo, props_path), daemon=True
    ).start()
    return jsonify(job_id=job_id)


@app.get("/api/jobs/<job_id>")
def api_job(job_id: str):
    job = _jobs.get(job_id)
    if not job:
        abort(404)
    return jsonify(
        {
            "id": job["id"],
            "demo": job["demo"],
            "status": job["status"],
            "progress": job["progress"],
            "log": job["log"][-60:],
            "error": job.get("error"),
            "output_url": job.get("output_url"),
            "size_mb": job.get("size_mb"),
        }
    )


def _safe_output(name: str) -> Path:
    # Only serve *.mp4 directly inside OUTPUT_DIR — no traversal.
    if not name.endswith(".mp4") or "/" in name or "\\" in name:
        abort(404)
    path = (OUTPUT_DIR / name).resolve()
    if not str(path).startswith(str(OUTPUT_DIR.resolve())) or not path.exists():
        abort(404)
    return path


@app.get("/video/<name>")
def video(name: str):
    # conditional=True gives Range support so the <video> tag can seek.
    return send_file(_safe_output(name), mimetype="video/mp4", conditional=True)


@app.get("/download/<name>")
def download(name: str):
    return send_file(_safe_output(name), mimetype="video/mp4", as_attachment=True)


# --------------------------------------------------------------------------- #
# Frontend (single inline page)
# --------------------------------------------------------------------------- #

INDEX_HTML = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1" />
<title>OpenMontage — Render Control Panel</title>
<style>
  :root { color-scheme: dark; }
  * { box-sizing: border-box; }
  body {
    margin: 0; font: 15px/1.5 -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
    background: #0b1020; color: #e6ebf5;
  }
  .wrap { max-width: 880px; margin: 0 auto; padding: 32px 20px 64px; }
  h1 { font-size: 22px; margin: 0 0 4px; letter-spacing: .2px; }
  .sub { color: #8b97b3; margin: 0 0 24px; font-size: 13px; }
  .card { background: #131a2e; border: 1px solid #243150; border-radius: 12px; padding: 18px; margin-bottom: 18px; }
  label { display: block; font-size: 12px; text-transform: uppercase; letter-spacing: .5px; color: #8b97b3; margin-bottom: 8px; }
  select, textarea, button {
    font: inherit; color: inherit; background: #0e1526; border: 1px solid #2c3a5e;
    border-radius: 8px; padding: 10px 12px; width: 100%;
  }
  textarea { min-height: 220px; font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size: 12px; resize: vertical; }
  .row { display: flex; gap: 12px; align-items: center; flex-wrap: wrap; }
  .row > * { flex: 1; }
  button.primary { background: #3b6fff; border-color: #3b6fff; color: #fff; font-weight: 600; cursor: pointer; flex: 0 0 auto; padding: 10px 22px; }
  button.primary:disabled { background: #2a3a63; border-color: #2a3a63; cursor: not-allowed; }
  button.ghost { background: transparent; border-color: #2c3a5e; cursor: pointer; flex: 0 0 auto; }
  .desc { color: #8b97b3; font-size: 13px; margin-top: 8px; }
  details { margin-top: 14px; }
  summary { cursor: pointer; color: #9fb0d6; font-size: 13px; }
  .bar { height: 8px; background: #0e1526; border-radius: 999px; overflow: hidden; border: 1px solid #2c3a5e; }
  .bar > i { display: block; height: 100%; width: 0; background: linear-gradient(90deg,#3b6fff,#5ee0c0); transition: width .3s; }
  pre.log { background: #080d1a; border: 1px solid #1c2742; border-radius: 8px; padding: 12px; max-height: 220px; overflow: auto; font-size: 12px; color: #9fb6e0; white-space: pre-wrap; word-break: break-word; }
  video { width: 100%; border-radius: 8px; background: #000; margin-top: 4px; }
  .status { font-size: 13px; margin: 10px 0; }
  .status.err { color: #ff7a85; }
  .status.ok { color: #5ee0c0; }
  .pill { display:inline-block; font-size:11px; padding:2px 8px; border-radius:999px; background:#1c2742; color:#9fb0d6; margin-left:8px; }
  a.dl { color:#5ee0c0; text-decoration:none; font-weight:600; }
  .muted { color:#5f6c8c; font-size:12px; }
</style>
</head>
<body>
<div class="wrap">
  <h1>OpenMontage · Render Control Panel</h1>
  <p class="sub" id="rootline">Zero-key Remotion demos — no API keys required.</p>

  <div class="card">
    <label for="demo">Demo</label>
    <div class="row">
      <select id="demo"></select>
      <button class="primary" id="render">▶ Render</button>
    </div>
    <div class="desc" id="desc"></div>

    <details id="advanced">
      <summary>Advanced — edit props JSON before rendering</summary>
      <p class="muted">Edited props are saved per-demo and used for this render. Leave as-is to use the shipped demo.</p>
      <textarea id="props" spellcheck="false"></textarea>
      <div class="row" style="margin-top:8px;">
        <button class="ghost" id="reset" style="flex:0 0 auto;">Reset to shipped props</button>
      </div>
    </details>
  </div>

  <div class="card" id="runcard" style="display:none;">
    <div class="status" id="status"></div>
    <div class="bar"><i id="barfill"></i></div>
    <details style="margin-top:12px;" id="logwrap" open>
      <summary>Render log</summary>
      <pre class="log" id="log"></pre>
    </details>
  </div>

  <div class="card" id="result" style="display:none;">
    <label>Result <span class="pill" id="size"></span></label>
    <video id="player" controls></video>
    <p style="margin:12px 0 0;"><a class="dl" id="dl" href="#">⬇ Download MP4</a></p>
  </div>
</div>

<script>
const $ = (id) => document.getElementById(id);
let pollTimer = null;

async function loadDemos() {
  const r = await fetch("/api/demos");
  const d = await r.json();
  if (d.root) $("rootline").textContent = "Operating on: " + d.root;
  const sel = $("demo");
  sel.innerHTML = "";
  if (!d.demos || !d.demos.length) {
    $("desc").textContent = d.error || "No demos found.";
    $("render").disabled = true;
    return;
  }
  for (const dm of d.demos) {
    const o = document.createElement("option");
    o.value = dm.name;
    o.textContent = dm.name + (dm.rendered ? "  (rendered)" : "");
    o.dataset.desc = dm.description;
    sel.appendChild(o);
  }
  sel.onchange = onDemoChange;
  onDemoChange();
}

async function onDemoChange() {
  const sel = $("demo");
  const opt = sel.selectedOptions[0];
  $("desc").textContent = opt ? opt.dataset.desc : "";
  await loadProps(sel.value);
}

async function loadProps(name) {
  try {
    const r = await fetch("/api/props/" + encodeURIComponent(name));
    $("props").value = await r.text();
  } catch (e) { $("props").value = ""; }
}

$("reset") && ($("reset").onclick = (e) => { e.preventDefault(); loadProps($("demo").value); });

$("render").onclick = async () => {
  const demo = $("demo").value;
  const shipped = $("props").defaultValue;
  let props = $("props").value;
  // Only send edited props if the user actually changed something.
  const body = { demo };
  // Always send current textarea content; backend compares against shipped file.
  // To avoid forcing a temp file when unchanged, send only if non-trivially edited.
  body.props = props;

  $("render").disabled = true;
  $("result").style.display = "none";
  $("runcard").style.display = "block";
  setStatus("Starting render…", "");
  setBar(2);
  $("log").textContent = "";

  let res;
  try {
    res = await (await fetch("/api/render", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    })).json();
  } catch (e) {
    setStatus("Failed to start: " + e, "err"); $("render").disabled = false; return;
  }
  if (res.error) { setStatus(res.error, "err"); $("render").disabled = false; return; }
  poll(res.job_id);
};

function poll(jobId) {
  clearInterval(pollTimer);
  pollTimer = setInterval(async () => {
    let j;
    try { j = await (await fetch("/api/jobs/" + jobId)).json(); }
    catch (e) { return; }
    $("log").textContent = (j.log || []).join("\n");
    $("log").scrollTop = $("log").scrollHeight;
    setBar(j.progress || 0);
    if (j.status === "running") {
      setStatus("Rendering " + j.demo + " — " + (j.progress || 0) + "%", "");
    } else if (j.status === "done") {
      clearInterval(pollTimer);
      setStatus("Done — " + j.demo, "ok");
      setBar(100);
      $("render").disabled = false;
      showResult(j);
    } else if (j.status === "error") {
      clearInterval(pollTimer);
      setStatus("Error: " + (j.error || "render failed"), "err");
      $("render").disabled = false;
    }
  }, 1000);
}

function showResult(j) {
  $("result").style.display = "block";
  $("size").textContent = j.size_mb ? j.size_mb + " MB" : "";
  const url = j.output_url + "?t=" + Date.now();
  $("player").src = url;
  $("dl").href = j.output_url.replace("/video/", "/download/");
  $("result").scrollIntoView({ behavior: "smooth", block: "nearest" });
}

function setStatus(t, cls) { const s = $("status"); s.textContent = t; s.className = "status " + (cls || ""); }
function setBar(p) { $("barfill").style.width = Math.max(0, Math.min(100, p)) + "%"; }

loadDemos();
</script>
</body>
</html>"""


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "8000"))
    host = os.environ.get("HOST", "127.0.0.1")
    print(f"OpenMontage Render Control Panel")
    print(f"  root:   {ROOT_DIR}")
    print(f"  open:   http://localhost:{port}")
    app.run(host=host, port=port, threaded=True)
