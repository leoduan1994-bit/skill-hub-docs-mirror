#!/usr/bin/env python3
"""OpenMontage Render Control Panel — a small web UI for the Explainer composition.

Drop this folder at the root of an OpenMontage checkout (so the layout is
`OpenMontage/openmontage-webui/app.py`) and run:

    pip install flask
    python openmontage-webui/app.py

Then open http://localhost:8000

What it does:
  * Demos      — render the checked-in zero-key Remotion demos.
  * My Works   — create your own videos from scratch: start from a template,
                 build scenes from a snippet palette, edit the props JSON,
                 save, render, preview, and download.
  * Templates  — full-work starters, per-scene snippets, and theme presets,
                 all valid against the "Explainer" composition's schema.

No API keys are required — everything renders from local Remotion components.
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

from flask import Flask, Response, abort, jsonify, request, send_file
from werkzeug.utils import secure_filename

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
    return here.parent


ROOT_DIR = _find_root()
COMPOSER_DIR = ROOT_DIR / "remotion-composer"
PROPS_DIR = COMPOSER_DIR / "public" / "demo-props"
OUTPUT_DIR = ROOT_DIR / "projects" / "demos" / "renders"
EDITED_PROPS_DIR = OUTPUT_DIR / "_webui_props"
WORKS_DIR = ROOT_DIR / "projects" / "custom-works"  # user-created works live here
UPLOADS_DIR = COMPOSER_DIR / "public" / "uploads"   # uploaded assets (served by staticFile)
PREVIEW_DIR = OUTPUT_DIR / "_webui_preview"          # cached Remotion Player bundle

# Uploadable asset kinds (extension -> kind).
IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp"}
VIDEO_EXTS = {".mp4", ".mov", ".webm", ".mkv", ".m4v"}
AUDIO_EXTS = {".mp3", ".wav", ".m4a", ".aac", ".ogg", ".flac"}


def _asset_kind(name: str) -> str | None:
    ext = Path(name).suffix.lower()
    if ext in IMAGE_EXTS:
        return "image"
    if ext in VIDEO_EXTS:
        return "video"
    if ext in AUDIO_EXTS:
        return "audio"
    return None

DEMO_DESCRIPTIONS = {
    "world-in-numbers": "Global scale story with titles, stats, and charts",
    "code-to-screen": "Developer workflow explainer with comparison and KPI cards",
    "focusflow-pitch": "Startup-style pitch built only from Remotion components",
}

# Themes understood by the Explainer composition (props.theme).
THEMES = [
    "flat-motion-graphics",
    "clean-professional",
    "minimalist-diagram",
    "anime-ghibli",
]

_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 _-]{0,59}$")


def _slug(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9_-]+", "-", name.strip()).strip("-").lower()


# --------------------------------------------------------------------------- #
# Template library — scene snippets + full-work starters.
# Every `cut` here is valid against Explainer.tsx's schema (see SceneRenderer).
# --------------------------------------------------------------------------- #

SCENE_TEMPLATES = [
    {"id": "hero_title", "label": "Hero title", "duration": 3.5,
     "cut": {"type": "hero_title", "text": "Your Big Title",
             "heroSubtitle": "A short supporting line", "backgroundColor": "#0F172A"}},
    {"id": "text_card", "label": "Text card", "duration": 3.0,
     "cut": {"type": "text_card", "text": "One clear idea on screen."}},
    {"id": "callout", "label": "Callout box", "duration": 4.0,
     "cut": {"type": "callout", "callout_type": "tip", "title": "Pro tip",
             "text": "Something worth highlighting for the viewer."}},
    {"id": "stat_card", "label": "Stat card", "duration": 3.0,
     "cut": {"type": "stat_card", "stat": "92%", "subtitle": "of users agree"}},
    {"id": "comparison", "label": "Comparison", "duration": 4.0,
     "cut": {"type": "comparison", "title": "Before vs After",
             "leftLabel": "Before", "leftValue": "3 days",
             "rightLabel": "After", "rightValue": "2 hours"}},
    {"id": "bar_chart", "label": "Bar chart", "duration": 4.0,
     "cut": {"type": "bar_chart", "title": "Quarterly revenue", "showValues": True,
             "chartData": [{"label": "Q1", "value": 40}, {"label": "Q2", "value": 55},
                           {"label": "Q3", "value": 70}, {"label": "Q4", "value": 90}]}},
    {"id": "line_chart", "label": "Line chart", "duration": 4.0,
     "cut": {"type": "line_chart", "title": "Growth", "showGrid": True, "showMarkers": True,
             "chartSeries": [{"label": "Users",
                              "data": [{"x": 1, "y": 10}, {"x": 2, "y": 25},
                                       {"x": 3, "y": 45}, {"x": 4, "y": 80}]}]}},
    {"id": "pie_chart", "label": "Pie / donut", "duration": 4.0,
     "cut": {"type": "pie_chart", "title": "Market share", "donut": True,
             "centerLabel": "Total", "centerValue": "100%", "showLegend": True,
             "chartData": [{"label": "A", "value": 45}, {"label": "B", "value": 30},
                           {"label": "C", "value": 25}]}},
    {"id": "kpi_grid", "label": "KPI grid", "duration": 3.5,
     "cut": {"type": "kpi_grid", "title": "At a glance", "columns": 3,
             "chartData": [{"label": "Revenue", "value": 1200000, "prefix": "$"},
                           {"label": "Users", "value": 48000},
                           {"label": "NPS", "value": 72}]}},
    {"id": "progress_bar", "label": "Progress bar", "duration": 3.0,
     "cut": {"type": "progress_bar", "title": "Completion", "progress": 0.75,
             "progressLabel": "75% done"}},
    {"id": "terminal_scene", "label": "Terminal", "duration": 5.0,
     "cut": {"type": "terminal_scene", "terminalTitle": "bash", "prompt": "$",
             "steps": [{"kind": "cmd", "text": "npm run build"},
                       {"kind": "out", "text": "Build complete in 4.2s"},
                       {"kind": "cmd", "text": "npm test"},
                       {"kind": "out", "text": "All tests passed"}]}},
]

SCENE_BY_ID = {s["id"]: s for s in SCENE_TEMPLATES}


def _timed(theme: str, scene_ids: list[str]) -> dict:
    """Assemble a full props object from a list of scene-template ids, timed back-to-back."""
    cuts, t = [], 0.0
    for i, sid in enumerate(scene_ids):
        tpl = SCENE_BY_ID[sid]
        cut = {"id": f"{sid}-{i + 1}", "source": "", **json.loads(json.dumps(tpl["cut"]))}
        cut["in_seconds"] = round(t, 2)
        cut["out_seconds"] = round(t + tpl["duration"], 2)
        cuts.append(cut)
        t += tpl["duration"]
    return {"theme": theme, "cuts": cuts}


STARTER_TEMPLATES = [
    {"id": "blank", "label": "Blank (one title)",
     "description": "A single hero title — the smallest valid work to build on.",
     "build": lambda: _timed("flat-motion-graphics", ["hero_title"])},
    {"id": "explainer", "label": "Explainer",
     "description": "Title → callout → bar chart → closing text.",
     "build": lambda: _timed("flat-motion-graphics",
                             ["hero_title", "callout", "bar_chart", "text_card"])},
    {"id": "data-story", "label": "Data story",
     "description": "Title → KPI grid → line chart → pie chart → stat.",
     "build": lambda: _timed("clean-professional",
                             ["hero_title", "kpi_grid", "line_chart", "pie_chart", "stat_card"])},
    {"id": "product-pitch", "label": "Product pitch",
     "description": "Title → comparison → progress → quote callout.",
     "build": lambda: _timed("minimalist-diagram",
                             ["hero_title", "comparison", "progress_bar", "callout"])},
]

STARTER_BY_ID = {s["id"]: s for s in STARTER_TEMPLATES}

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


def discover_works() -> dict[str, Path]:
    if not WORKS_DIR.exists():
        return {}
    return {p.stem: p for p in sorted(WORKS_DIR.glob("*.json"))}


def _which(*names: str) -> str | None:
    for name in names:
        found = shutil.which(name)
        if found:
            return found
    return None


def _release(job_id: str) -> None:
    global _active_job
    with _jobs_lock:
        if _active_job == job_id:
            _active_job = None


def _run_render(job_id: str, props_path: Path, output_path: Path) -> None:
    """Worker thread: run the Remotion render and stream output into the job."""
    job = _jobs[job_id]
    npx = _which("npx.cmd", "npx", "npx.exe")
    if not npx:
        job["status"] = "error"
        job["error"] = "npx not found on PATH (install Node.js 18+)."
        _release(job_id)
        return

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
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
            cmd, cwd=COMPOSER_DIR, stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT, text=True, bufsize=1,
        )
        for line in proc.stdout:  # type: ignore[union-attr]
            line = line.rstrip("\n")
            if not line:
                continue
            job["log"].append(line)
            del job["log"][:-400]
            m = _PROGRESS_RE.search(line)
            if m and int(m.group(2)):
                job["progress"] = max(job["progress"], min(99, int(int(m.group(1)) / int(m.group(2)) * 100)))
        proc.wait()
        if proc.returncode == 0 and output_path.exists():
            job["status"] = "done"
            job["progress"] = 100
            job["output_url"] = f"/video/{output_path.name}"
            job["size_mb"] = round(output_path.stat().st_size / (1024 * 1024), 1)
        else:
            job["status"] = "error"
            job["error"] = f"Render exited with code {proc.returncode}."
    except Exception as exc:  # noqa: BLE001
        job["status"] = "error"
        job["error"] = str(exc)
        job["log"].append(f"[exception] {exc}")
    finally:
        _release(job_id)


def _launch(props_path: Path, output_path: Path, label: str):
    """Create a job under the single-render lock and start it. Returns (job_id, None) or (None, (msg, code))."""
    global _active_job
    with _jobs_lock:
        if _active_job is not None:
            return None, ("A render is already in progress. Please wait.", 409)
        job_id = uuid.uuid4().hex[:12]
        _active_job = job_id
        _jobs[job_id] = {
            "id": job_id, "label": label, "status": "running", "progress": 0,
            "log": [], "started": time.time(), "output_url": None,
        }
    threading.Thread(target=_run_render, args=(job_id, props_path, output_path), daemon=True).start()
    return job_id, None


def _resolve_props(base_path: Path, edited: str | None, tag: str) -> tuple[Path | None, tuple | None]:
    """Return the props file to render: the edited JSON (written to a temp file) or the base file."""
    if edited is not None and edited.strip():
        try:
            parsed = json.loads(edited)
        except json.JSONDecodeError as exc:
            return None, (f"Props is not valid JSON: {exc}", 400)
        if not isinstance(parsed.get("cuts"), list) or not parsed["cuts"]:
            return None, ("Props must define a non-empty 'cuts' array.", 400)
        EDITED_PROPS_DIR.mkdir(parents=True, exist_ok=True)
        path = EDITED_PROPS_DIR / f"{tag}.json"
        path.write_text(json.dumps(parsed, indent=2), encoding="utf-8")
        return path, None
    if not base_path.exists():
        return None, ("No props found to render.", 400)
    return base_path, None


# --------------------------------------------------------------------------- #
# Live preview — bundle the Explainer composition + Remotion <Player> for the
# browser with esbuild, so the page can play props live without a full render.
# --------------------------------------------------------------------------- #

_PREVIEW_ENTRY_TSX = r"""
import React from "react";
import { createRoot } from "react-dom/client";
import { Player } from "@remotion/player";
import { Explainer } from "./src/Explainer";

function computeDuration(props: any): number {
  const cuts = props?.cuts || [];
  if (!cuts.length) return 30 * 60;
  const lastEnd = Math.max(...cuts.map((c: any) => c.out_seconds || 0));
  return Math.ceil((lastEnd + 1) * 30);
}
let root: any = null;
function mount(props: any) {
  const el = document.getElementById("player-root");
  if (!el) return;
  if (!root) root = createRoot(el);
  root.render(
    React.createElement(Player as any, {
      component: Explainer,
      inputProps: props,
      durationInFrames: computeDuration(props),
      compositionWidth: 1920,
      compositionHeight: 1080,
      fps: 30,
      style: { width: "100%", borderRadius: 8 },
      controls: true,
      acknowledgeRemotionLicense: true,
    })
  );
}
(window as any).OMPreview = { mount };
window.addEventListener("message", (e: any) => {
  if (e?.data?.type === "om-preview" && e.data.props) {
    try { mount(e.data.props); } catch (err) { console.error(err); }
  }
});
"""

_preview_lock = threading.Lock()


def _esbuild_bin() -> str | None:
    for cand in (COMPOSER_DIR / "node_modules" / ".bin" / "esbuild",
                 COMPOSER_DIR / "node_modules" / ".bin" / "esbuild.cmd"):
        if cand.exists():
            return str(cand)
    return _which("esbuild")


def build_preview_bundle(force: bool = False) -> tuple[Path | None, str | None]:
    """Build (and cache) the browser bundle. Returns (bundle_path, error)."""
    bundle = PREVIEW_DIR / "bundle.js"
    with _preview_lock:
        if bundle.exists() and not force:
            return bundle, None
        esbuild = _esbuild_bin()
        if not esbuild:
            return None, ("esbuild not found. Run `cd remotion-composer && npm install` "
                          "so the live preview can be bundled.")
        PREVIEW_DIR.mkdir(parents=True, exist_ok=True)
        entry = COMPOSER_DIR / ".om-preview-entry.tsx"
        entry.write_text(_PREVIEW_ENTRY_TSX, encoding="utf-8")
        try:
            proc = subprocess.run(
                [esbuild, entry.name, "--bundle", f"--outfile={bundle}",
                 "--loader:.tsx=tsx", "--jsx=automatic", "--format=iife",
                 "--define:process.env.NODE_ENV=\"production\"", "--log-level=warning"],
                cwd=COMPOSER_DIR, capture_output=True, text=True, timeout=300,
            )
            if proc.returncode != 0:
                return None, f"esbuild failed:\n{proc.stderr[-2000:]}"
            return bundle, None
        except Exception as exc:  # noqa: BLE001
            return None, str(exc)
        finally:
            entry.unlink(missing_ok=True)


# --------------------------------------------------------------------------- #
# Flask app
# --------------------------------------------------------------------------- #

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 256 * 1024 * 1024  # 256 MB upload cap


@app.get("/")
def index() -> Response:
    return Response(INDEX_HTML, mimetype="text/html")


@app.get("/api/demos")
def api_demos():
    demos = discover_demos()
    out = [
        {"name": n, "description": DEMO_DESCRIPTIONS.get(n, "Checked-in Remotion demo"),
         "rendered": (OUTPUT_DIR / f"{n}.mp4").exists()}
        for n in demos
    ]
    return jsonify(root=str(ROOT_DIR), demos=out,
                   error=None if demos else f"No demo props in {PROPS_DIR}.")


@app.get("/api/props/<demo>")
def api_props(demo: str):
    demos = discover_demos()
    if demo not in demos:
        abort(404)
    return Response(demos[demo].read_text(encoding="utf-8"), mimetype="application/json")


@app.get("/api/templates")
def api_templates():
    return jsonify(
        themes=THEMES,
        scenes=[{"id": s["id"], "label": s["label"], "duration": s["duration"], "cut": s["cut"]}
                for s in SCENE_TEMPLATES],
        starters=[{"id": s["id"], "label": s["label"], "description": s["description"]}
                  for s in STARTER_TEMPLATES],
    )


# ---- Custom works CRUD ---------------------------------------------------- #

@app.get("/api/works")
def api_works():
    works = discover_works()
    out = [{"name": n, "rendered": (OUTPUT_DIR / f"work-{n}.mp4").exists()} for n in works]
    return jsonify(works=out, dir=str(WORKS_DIR))


@app.post("/api/works")
def api_create_work():
    payload = request.get_json(silent=True) or {}
    name = (payload.get("name") or "").strip()
    if not _NAME_RE.match(name):
        return jsonify(error="Name must be 1-60 chars: letters, numbers, spaces, - or _."), 400
    slug = _slug(name)
    if not slug:
        return jsonify(error="Name produced an empty identifier."), 400
    WORKS_DIR.mkdir(parents=True, exist_ok=True)
    path = WORKS_DIR / f"{slug}.json"
    if path.exists():
        return jsonify(error=f"A work named '{slug}' already exists."), 409
    starter_id = payload.get("starter") or "blank"
    starter = STARTER_BY_ID.get(starter_id) or STARTER_BY_ID["blank"]
    props = starter["build"]()
    path.write_text(json.dumps(props, indent=2), encoding="utf-8")
    return jsonify(name=slug)


@app.get("/api/works/<name>")
def api_get_work(name: str):
    works = discover_works()
    if name not in works:
        abort(404)
    return Response(works[name].read_text(encoding="utf-8"), mimetype="application/json")


@app.put("/api/works/<name>")
def api_save_work(name: str):
    works = discover_works()
    if name not in works:
        abort(404)
    payload = request.get_json(silent=True) or {}
    try:
        parsed = json.loads(payload.get("props", ""))
    except json.JSONDecodeError as exc:
        return jsonify(error=f"Props is not valid JSON: {exc}"), 400
    if not isinstance(parsed.get("cuts"), list) or not parsed["cuts"]:
        return jsonify(error="Props must define a non-empty 'cuts' array."), 400
    works[name].write_text(json.dumps(parsed, indent=2), encoding="utf-8")
    return jsonify(ok=True)


@app.delete("/api/works/<name>")
def api_delete_work(name: str):
    works = discover_works()
    if name not in works:
        abort(404)
    works[name].unlink()
    return jsonify(ok=True)


# ---- Render (demos and works share this) ---------------------------------- #

@app.post("/api/render")
def api_render():
    payload = request.get_json(silent=True) or {}
    kind = payload.get("kind", "demo")
    name = payload.get("name") or payload.get("demo")  # 'demo' kept for back-compat
    edited = payload.get("props")

    if kind == "work":
        works = discover_works()
        if name not in works:
            return jsonify(error=f"Unknown work '{name}'."), 400
        base, output = works[name], OUTPUT_DIR / f"work-{name}.mp4"
        tag = f"work-{name}"
    else:
        demos = discover_demos()
        if name not in demos:
            return jsonify(error=f"Unknown demo '{name}'."), 400
        base, output = demos[name], OUTPUT_DIR / f"{name}.mp4"
        tag = f"demo-{name}"

    props_path, err = _resolve_props(base, edited, tag)
    if err:
        return jsonify(error=err[0]), err[1]
    job_id, err = _launch(props_path, output, label=f"{kind}:{name}")
    if err:
        return jsonify(error=err[0]), err[1]
    return jsonify(job_id=job_id)


@app.get("/api/jobs/<job_id>")
def api_job(job_id: str):
    job = _jobs.get(job_id)
    if not job:
        abort(404)
    return jsonify({
        "id": job["id"], "label": job.get("label"), "status": job["status"],
        "progress": job["progress"], "log": job["log"][-60:], "error": job.get("error"),
        "output_url": job.get("output_url"), "size_mb": job.get("size_mb"),
    })


# ---- Live preview bundle -------------------------------------------------- #

@app.get("/preview.bundle.js")
def preview_bundle():
    bundle, err = build_preview_bundle(force=request.args.get("rebuild") == "1")
    if err:
        # 200 with a console.error so a <script> load surfaces the reason in-page.
        return Response(f"console.error({json.dumps('OpenMontage live preview unavailable: ' + err)});",
                        mimetype="application/javascript"), 200
    return send_file(bundle, mimetype="application/javascript", conditional=True)


# ---- Uploadable assets ---------------------------------------------------- #

@app.get("/uploads/<name>")
def uploads(name: str):
    if "/" in name or "\\" in name:
        abort(404)
    path = (UPLOADS_DIR / name).resolve()
    if not str(path).startswith(str(UPLOADS_DIR.resolve())) or not path.exists():
        abort(404)
    return send_file(path, conditional=True)


def _list_assets() -> list[dict]:
    if not UPLOADS_DIR.exists():
        return []
    out = []
    for p in sorted(UPLOADS_DIR.iterdir()):
        if p.is_file() and _asset_kind(p.name):
            out.append({"name": p.name, "path": f"uploads/{p.name}",
                        "kind": _asset_kind(p.name),
                        "size_kb": round(p.stat().st_size / 1024, 1)})
    return out


@app.get("/api/assets")
def api_assets():
    return jsonify(assets=_list_assets(), dir=str(UPLOADS_DIR))


@app.post("/api/assets")
def api_upload_asset():
    f = request.files.get("file")
    if not f or not f.filename:
        return jsonify(error="No file provided."), 400
    safe = secure_filename(f.filename)
    if not safe or _asset_kind(safe) is None:
        return jsonify(error="Unsupported file type. Use image, video, or audio."), 400
    UPLOADS_DIR.mkdir(parents=True, exist_ok=True)
    dest = UPLOADS_DIR / safe
    # Avoid clobbering: add a numeric suffix if the name already exists.
    stem, ext = Path(safe).stem, Path(safe).suffix
    i = 1
    while dest.exists():
        dest = UPLOADS_DIR / f"{stem}-{i}{ext}"
        i += 1
    f.save(str(dest))
    return jsonify(name=dest.name, path=f"uploads/{dest.name}", kind=_asset_kind(dest.name))


@app.delete("/api/assets/<name>")
def api_delete_asset(name: str):
    if "/" in name or "\\" in name:
        abort(404)
    path = (UPLOADS_DIR / name).resolve()
    if not str(path).startswith(str(UPLOADS_DIR.resolve())) or not path.exists():
        abort(404)
    path.unlink()
    return jsonify(ok=True)


def _safe_output(name: str) -> Path:
    if not name.endswith(".mp4") or "/" in name or "\\" in name:
        abort(404)
    path = (OUTPUT_DIR / name).resolve()
    if not str(path).startswith(str(OUTPUT_DIR.resolve())) or not path.exists():
        abort(404)
    return path


@app.get("/video/<name>")
def video(name: str):
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
  body { margin: 0; font: 15px/1.5 -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; background: #0b1020; color: #e6ebf5; }
  .wrap { max-width: 900px; margin: 0 auto; padding: 30px 20px 64px; }
  h1 { font-size: 22px; margin: 0 0 4px; }
  .sub { color: #8b97b3; margin: 0 0 20px; font-size: 13px; }
  .tabs { display: flex; gap: 6px; margin-bottom: 18px; border-bottom: 1px solid #243150; }
  .tab { padding: 9px 16px; cursor: pointer; color: #8b97b3; border-bottom: 2px solid transparent; font-weight: 600; font-size: 14px; }
  .tab.active { color: #fff; border-bottom-color: #3b6fff; }
  .card { background: #131a2e; border: 1px solid #243150; border-radius: 12px; padding: 18px; margin-bottom: 16px; }
  label { display: block; font-size: 12px; text-transform: uppercase; letter-spacing: .5px; color: #8b97b3; margin-bottom: 8px; }
  select, textarea, button, input[type=text] { font: inherit; color: inherit; background: #0e1526; border: 1px solid #2c3a5e; border-radius: 8px; padding: 10px 12px; width: 100%; }
  textarea { min-height: 300px; font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size: 12px; resize: vertical; }
  .row { display: flex; gap: 10px; align-items: center; flex-wrap: wrap; }
  .row > * { flex: 1; }
  button.primary { background: #3b6fff; border-color: #3b6fff; color: #fff; font-weight: 600; cursor: pointer; flex: 0 0 auto; padding: 10px 22px; }
  button.primary:disabled { background: #2a3a63; border-color: #2a3a63; cursor: not-allowed; }
  button.ghost { background: transparent; border-color: #2c3a5e; cursor: pointer; flex: 0 0 auto; }
  button.danger { background: transparent; border-color: #5a2030; color: #ff8a96; cursor: pointer; flex: 0 0 auto; }
  .desc { color: #8b97b3; font-size: 13px; margin-top: 8px; }
  .bar { height: 8px; background: #0e1526; border-radius: 999px; overflow: hidden; border: 1px solid #2c3a5e; }
  .bar > i { display: block; height: 100%; width: 0; background: linear-gradient(90deg,#3b6fff,#5ee0c0); transition: width .3s; }
  pre.log { background: #080d1a; border: 1px solid #1c2742; border-radius: 8px; padding: 12px; max-height: 220px; overflow: auto; font-size: 12px; color: #9fb6e0; white-space: pre-wrap; word-break: break-word; }
  video { width: 100%; border-radius: 8px; background: #000; margin-top: 4px; }
  .status { font-size: 13px; margin: 10px 0; } .status.err { color: #ff7a85; } .status.ok { color: #5ee0c0; }
  .pill { display:inline-block; font-size:11px; padding:2px 8px; border-radius:999px; background:#1c2742; color:#9fb0d6; margin-left:8px; }
  a.dl { color:#5ee0c0; text-decoration:none; font-weight:600; }
  .muted { color:#5f6c8c; font-size:12px; margin:6px 0; }
  .hint { color:#7c89ab; font-size:12px; }
  h3 { font-size: 13px; text-transform: uppercase; letter-spacing:.5px; color:#8b97b3; margin: 0 0 10px; }
  .sp { margin-top: 14px; }
  .toast { position: fixed; bottom: 18px; left: 50%; transform: translateX(-50%); background:#1c2742; border:1px solid #2c3a5e; padding:10px 16px; border-radius:8px; font-size:13px; opacity:0; transition:opacity .3s; pointer-events:none; }
  .toast.show { opacity: 1; }
</style>
</head>
<body>
<div class="wrap">
  <h1>OpenMontage · Render Control Panel</h1>
  <p class="sub" id="rootline">Zero-key Remotion videos — no API keys required.</p>

  <div class="tabs">
    <div class="tab active" data-tab="demos">Demos</div>
    <div class="tab" data-tab="works">My Works</div>
  </div>

  <!-- ===================== DEMOS TAB ===================== -->
  <div id="tab-demos">
    <div class="card">
      <label for="demo">Demo</label>
      <div class="row">
        <select id="demo"></select>
        <button class="ghost" id="previewDemo" style="flex:0 0 auto;">👁 Live preview</button>
        <button class="primary" id="renderDemo">▶ Render</button>
      </div>
      <div class="desc" id="demoDesc"></div>
      <details class="sp" id="demoAdv">
        <summary class="hint">Advanced — edit props JSON before rendering</summary>
        <p class="muted">Edited props are used for this render only. Leave as-is for the shipped demo.</p>
        <textarea id="demoProps" spellcheck="false"></textarea>
        <div class="row sp"><button class="ghost" id="demoReset" style="flex:0 0 auto;">Reset to shipped props</button></div>
      </details>
    </div>
  </div>

  <!-- ===================== WORKS TAB ===================== -->
  <div id="tab-works" style="display:none;">
    <div class="card">
      <h3>Create a new work</h3>
      <div class="row">
        <input type="text" id="newName" placeholder="Work name (e.g. my-launch-video)" />
        <select id="starter" style="max-width:240px;"></select>
        <button class="ghost" id="createWork" style="flex:0 0 auto;">Create</button>
      </div>
      <div class="desc" id="starterDesc"></div>
    </div>

    <div class="card" id="worksEditor" style="display:none;">
      <h3>Edit work</h3>
      <div class="row">
        <select id="work"></select>
        <button class="danger" id="deleteWork" style="flex:0 0 auto;">Delete</button>
      </div>

      <div class="row sp">
        <div style="flex:1;">
          <label for="theme">Theme</label>
          <select id="theme"></select>
        </div>
        <div style="flex:2;">
          <label for="scenePalette">Insert scene</label>
          <div class="row">
            <select id="scenePalette"></select>
            <button class="ghost" id="insertScene" style="flex:0 0 auto;">+ Add</button>
          </div>
        </div>
      </div>

      <details class="sp" id="assetsBox">
        <summary class="hint">Assets — upload audio / images / video to use in this work</summary>
        <p class="muted">Files are saved to <code>remotion-composer/public/uploads/</code> and referenced by their path. They load in both live preview and final render.</p>
        <div class="row">
          <input type="file" id="assetFile" accept="image/*,video/*,audio/*" style="flex:1;" />
          <button class="ghost" id="uploadAsset" style="flex:0 0 auto;">⬆ Upload</button>
        </div>
        <div id="assetList" class="sp"></div>
      </details>

      <label class="sp" for="workProps">Props JSON</label>
      <textarea id="workProps" spellcheck="false"></textarea>
      <p class="muted" id="workMeta"></p>
      <div class="row sp">
        <button class="ghost" id="saveWork" style="flex:0 0 auto;">💾 Save</button>
        <button class="ghost" id="previewWork" style="flex:0 0 auto;">👁 Live preview</button>
        <button class="primary" id="renderWork" style="flex:0 0 auto;">▶ Render</button>
      </div>
    </div>

    <div class="card" id="worksEmpty">
      <p class="muted" style="margin:0;">No works yet — create one above to start building your own video.</p>
    </div>
  </div>

  <!-- ===================== SHARED LIVE PREVIEW ===================== -->
  <div class="card" id="previewCard" style="display:none;">
    <label>Live preview <span class="hint" id="previewStatus"></span></label>
    <div id="player-root" style="background:#000;border-radius:8px;min-height:120px;"></div>
    <p class="muted">Plays in your browser via Remotion Player — no render needed. Reflects the current props each time you click <em>Live preview</em>.</p>
  </div>

  <!-- ===================== SHARED RUN + RESULT ===================== -->
  <div class="card" id="runcard" style="display:none;">
    <div class="status" id="status"></div>
    <div class="bar"><i id="barfill"></i></div>
    <details class="sp" id="logwrap" open>
      <summary class="hint">Render log</summary>
      <pre class="log" id="log"></pre>
    </details>
  </div>

  <div class="card" id="result" style="display:none;">
    <label>Result <span class="pill" id="size"></span></label>
    <video id="player" controls></video>
    <p style="margin:12px 0 0;"><a class="dl" id="dl" href="#">⬇ Download MP4</a></p>
  </div>
</div>
<div class="toast" id="toast"></div>

<script>
const $ = (id) => document.getElementById(id);
let pollTimer = null;
let TEMPLATES = { themes: [], scenes: [], starters: [] };

function toast(msg) { const t = $("toast"); t.textContent = msg; t.classList.add("show"); setTimeout(() => t.classList.remove("show"), 2200); }

// ---- Tabs ----
document.querySelectorAll(".tab").forEach((el) => {
  el.onclick = () => {
    document.querySelectorAll(".tab").forEach((t) => t.classList.remove("active"));
    el.classList.add("active");
    const tab = el.dataset.tab;
    $("tab-demos").style.display = tab === "demos" ? "" : "none";
    $("tab-works").style.display = tab === "works" ? "" : "none";
  };
});

// ---- Demos ----
async function loadDemos() {
  const d = await (await fetch("/api/demos")).json();
  if (d.root) $("rootline").textContent = "Operating on: " + d.root;
  const sel = $("demo"); sel.innerHTML = "";
  if (!d.demos || !d.demos.length) { $("demoDesc").textContent = d.error || "No demos."; $("renderDemo").disabled = true; return; }
  for (const dm of d.demos) {
    const o = document.createElement("option");
    o.value = dm.name; o.textContent = dm.name + (dm.rendered ? "  (rendered)" : "");
    o.dataset.desc = dm.description; sel.appendChild(o);
  }
  sel.onchange = onDemoChange; onDemoChange();
}
async function onDemoChange() {
  const opt = $("demo").selectedOptions[0];
  $("demoDesc").textContent = opt ? opt.dataset.desc : "";
  try { $("demoProps").value = await (await fetch("/api/props/" + encodeURIComponent($("demo").value))).text(); } catch (e) { $("demoProps").value = ""; }
}
$("demoReset").onclick = (e) => { e.preventDefault(); onDemoChange(); };
$("renderDemo").onclick = () => startRender({ kind: "demo", name: $("demo").value, props: $("demoProps").value }, $("renderDemo"));

// ---- Templates + Works ----
async function loadTemplates() {
  TEMPLATES = await (await fetch("/api/templates")).json();
  const st = $("starter"); st.innerHTML = "";
  for (const s of TEMPLATES.starters) { const o = document.createElement("option"); o.value = s.id; o.textContent = s.label; o.dataset.desc = s.description; st.appendChild(o); }
  st.onchange = () => { const o = st.selectedOptions[0]; $("starterDesc").textContent = o ? o.dataset.desc : ""; };
  st.onchange();
  const th = $("theme"); th.innerHTML = "";
  for (const t of TEMPLATES.themes) { const o = document.createElement("option"); o.value = t; o.textContent = t; th.appendChild(o); }
  th.onchange = applyTheme;
  const pal = $("scenePalette"); pal.innerHTML = "";
  for (const s of TEMPLATES.scenes) { const o = document.createElement("option"); o.value = s.id; o.textContent = s.label + " (" + s.duration + "s)"; pal.appendChild(o); }
}

async function loadWorks(selectName) {
  const d = await (await fetch("/api/works")).json();
  const sel = $("work"); sel.innerHTML = "";
  if (!d.works.length) { $("worksEditor").style.display = "none"; $("worksEmpty").style.display = ""; return; }
  for (const w of d.works) { const o = document.createElement("option"); o.value = w.name; o.textContent = w.name + (w.rendered ? "  (rendered)" : ""); sel.appendChild(o); }
  $("worksEmpty").style.display = "none"; $("worksEditor").style.display = "";
  if (selectName) sel.value = selectName;
  sel.onchange = loadWorkProps;
  await loadWorkProps();
  loadAssets();
}
async function loadWorkProps() {
  const name = $("work").value;
  try { $("workProps").value = await (await fetch("/api/works/" + encodeURIComponent(name))).text(); } catch (e) { $("workProps").value = ""; }
  syncThemeFromProps(); updateMeta();
}
function currentProps() { try { return JSON.parse($("workProps").value); } catch (e) { return null; } }
function updateMeta() {
  const p = currentProps();
  if (!p) { $("workMeta").textContent = "⚠ JSON is currently invalid — fix it before saving/rendering."; return; }
  const cuts = (p.cuts || []); const dur = cuts.length ? Math.max(...cuts.map((c) => c.out_seconds || 0)) : 0;
  $("workMeta").textContent = cuts.length + " scene(s), ~" + dur.toFixed(1) + "s · theme: " + (p.theme || "default");
}
function syncThemeFromProps() { const p = currentProps(); if (p && p.theme && TEMPLATES.themes.includes(p.theme)) $("theme").value = p.theme; }
function applyTheme() {
  const p = currentProps(); if (!p) { toast("Fix JSON first"); return; }
  p.theme = $("theme").value; $("workProps").value = JSON.stringify(p, null, 2); updateMeta();
}
$("insertScene").onclick = () => {
  const p = currentProps(); if (!p) { toast("Fix JSON first"); return; }
  if (!Array.isArray(p.cuts)) p.cuts = [];
  const tpl = TEMPLATES.scenes.find((s) => s.id === $("scenePalette").value);
  const start = p.cuts.length ? Math.max(...p.cuts.map((c) => c.out_seconds || 0)) : 0;
  const cut = Object.assign({ id: tpl.id + "-" + (p.cuts.length + 1), source: "" }, JSON.parse(JSON.stringify(tpl.cut)));
  cut.in_seconds = Math.round(start * 100) / 100;
  cut.out_seconds = Math.round((start + tpl.duration) * 100) / 100;
  p.cuts.push(cut);
  $("workProps").value = JSON.stringify(p, null, 2); updateMeta();
  toast("Added " + tpl.label);
};
$("workProps").addEventListener("input", updateMeta);

$("createWork").onclick = async () => {
  const name = $("newName").value.trim();
  if (!name) { toast("Enter a name"); return; }
  const r = await (await fetch("/api/works", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ name, starter: $("starter").value }) })).json();
  if (r.error) { toast(r.error); return; }
  $("newName").value = ""; toast("Created " + r.name); await loadWorks(r.name);
};
$("deleteWork").onclick = async () => {
  const name = $("work").value;
  if (!confirm("Delete work '" + name + "'? This removes its props file.")) return;
  const r = await (await fetch("/api/works/" + encodeURIComponent(name), { method: "DELETE" })).json();
  if (r.error) { toast(r.error); return; }
  toast("Deleted " + name); await loadWorks();
};
$("saveWork").onclick = async () => {
  const name = $("work").value;
  const r = await (await fetch("/api/works/" + encodeURIComponent(name), { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ props: $("workProps").value }) })).json();
  if (r.error) { toast(r.error); return; }
  toast("Saved " + name);
};
$("renderWork").onclick = () => startRender({ kind: "work", name: $("work").value, props: $("workProps").value }, $("renderWork"));

// ---- Live preview (Remotion Player, no render) ----
let previewLoaded = false, previewLoading = null;
function setPreviewStatus(t) { $("previewStatus").textContent = t ? "— " + t : ""; }
function ensurePreviewBundle() {
  if (previewLoaded) return Promise.resolve(true);
  if (previewLoading) return previewLoading;
  previewLoading = new Promise((resolve) => {
    const s = document.createElement("script");
    s.src = "/preview.bundle.js?t=" + Date.now();
    s.onload = () => { previewLoaded = !!window.OMPreview; resolve(previewLoaded); };
    s.onerror = () => resolve(false);
    document.body.appendChild(s);
  });
  return previewLoading;
}
async function livePreview(propsText) {
  let props; try { props = JSON.parse(propsText); } catch (e) { toast("Fix JSON first"); return; }
  $("previewCard").style.display = "block";
  setPreviewStatus("loading engine…");
  const ok = await ensurePreviewBundle();
  if (!ok || !window.OMPreview) { setPreviewStatus("unavailable — run `npm install` in remotion-composer, see server log"); return; }
  setPreviewStatus("");
  try { window.OMPreview.mount(props); } catch (e) { setPreviewStatus("error: " + e); return; }
  $("previewCard").scrollIntoView({ behavior: "smooth", block: "nearest" });
}
$("previewDemo").onclick = () => livePreview($("demoProps").value);
$("previewWork").onclick = () => livePreview($("workProps").value);

// ---- Uploadable assets ----
async function loadAssets() {
  const d = await (await fetch("/api/assets")).json();
  const box = $("assetList"); box.innerHTML = "";
  if (!d.assets.length) { box.innerHTML = '<p class="muted" style="margin:0;">No assets yet.</p>'; return; }
  for (const a of d.assets) {
    const row = document.createElement("div");
    row.className = "row"; row.style.marginBottom = "6px"; row.style.alignItems = "center";
    const icon = a.kind === "audio" ? "🎵" : a.kind === "video" ? "🎬" : "🖼";
    let actions = "";
    if (a.kind === "audio") actions = `<button class="ghost om-a" data-act="music" style="flex:0 0 auto;">Set music</button><button class="ghost om-a" data-act="narration" style="flex:0 0 auto;">Set narration</button>`;
    else actions = `<button class="ghost om-a" data-act="scene" style="flex:0 0 auto;">Add as scene</button>`;
    row.innerHTML = `<span style="flex:1;font-size:13px;">${icon} ${a.name} <span class="muted">(${a.size_kb} KB · ${a.path})</span></span>${actions}<button class="ghost om-a" data-act="copy" style="flex:0 0 auto;">Copy</button><button class="danger om-a" data-act="del" style="flex:0 0 auto;">✕</button>`;
    row.querySelectorAll(".om-a").forEach((b) => { b.onclick = () => assetAction(b.dataset.act, a); });
    box.appendChild(row);
  }
}
function assetAction(act, a) {
  if (act === "copy") { navigator.clipboard?.writeText(a.path); toast("Copied " + a.path); return; }
  if (act === "del") { fetch("/api/assets/" + encodeURIComponent(a.name), { method: "DELETE" }).then(() => { toast("Deleted"); loadAssets(); }); return; }
  const p = currentProps(); if (!p) { toast("Fix JSON first"); return; }
  if (act === "scene") {
    if (!Array.isArray(p.cuts)) p.cuts = [];
    const start = p.cuts.length ? Math.max(...p.cuts.map((c) => c.out_seconds || 0)) : 0;
    const dur = a.kind === "video" ? 6 : 4;
    p.cuts.push({ id: a.kind + "-" + (p.cuts.length + 1), source: a.path, in_seconds: Math.round(start * 100) / 100, out_seconds: Math.round((start + dur) * 100) / 100 });
  } else if (act === "music") {
    p.audio = p.audio || {}; p.audio.music = Object.assign({ volume: 0.15, loop: true, fadeInSeconds: 2, fadeOutSeconds: 3 }, p.audio.music || {}, { src: a.path });
  } else if (act === "narration") {
    p.audio = p.audio || {}; p.audio.narration = Object.assign({ volume: 1 }, p.audio.narration || {}, { src: a.path });
  }
  $("workProps").value = JSON.stringify(p, null, 2); updateMeta();
  toast(act === "scene" ? "Added scene" : act === "music" ? "Set as music" : "Set as narration");
}
$("uploadAsset").onclick = async () => {
  const f = $("assetFile").files[0];
  if (!f) { toast("Choose a file"); return; }
  const fd = new FormData(); fd.append("file", f);
  const r = await (await fetch("/api/assets", { method: "POST", body: fd })).json();
  if (r.error) { toast(r.error); return; }
  $("assetFile").value = ""; toast("Uploaded " + r.name); loadAssets();
};

// ---- Shared render flow ----
async function startRender(body, btn) {
  btn.disabled = true; $("result").style.display = "none"; $("runcard").style.display = "block";
  setStatus("Starting render…", ""); setBar(2); $("log").textContent = "";
  let res;
  try { res = await (await fetch("/api/render", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) })).json(); }
  catch (e) { setStatus("Failed to start: " + e, "err"); btn.disabled = false; return; }
  if (res.error) { setStatus(res.error, "err"); btn.disabled = false; return; }
  poll(res.job_id, btn);
}
function poll(jobId, btn) {
  clearInterval(pollTimer);
  pollTimer = setInterval(async () => {
    let j; try { j = await (await fetch("/api/jobs/" + jobId)).json(); } catch (e) { return; }
    $("log").textContent = (j.log || []).join("\n"); $("log").scrollTop = $("log").scrollHeight;
    setBar(j.progress || 0);
    if (j.status === "running") { setStatus("Rendering " + (j.label || "") + " — " + (j.progress || 0) + "%", ""); }
    else if (j.status === "done") { clearInterval(pollTimer); setStatus("Done — " + (j.label || ""), "ok"); setBar(100); btn.disabled = false; showResult(j); refreshLists(); }
    else if (j.status === "error") { clearInterval(pollTimer); setStatus("Error: " + (j.error || "render failed"), "err"); btn.disabled = false; }
  }, 1000);
}
function showResult(j) {
  $("result").style.display = "block";
  $("size").textContent = j.size_mb ? j.size_mb + " MB" : "";
  $("player").src = j.output_url + "?t=" + Date.now();
  $("dl").href = j.output_url.replace("/video/", "/download/");
  $("result").scrollIntoView({ behavior: "smooth", block: "nearest" });
}
function refreshLists() { loadDemos(); if ($("worksEditor").style.display !== "none") { const cur = $("work").value; loadWorks(cur); } }
function setStatus(t, cls) { const s = $("status"); s.textContent = t; s.className = "status " + (cls || ""); }
function setBar(p) { $("barfill").style.width = Math.max(0, Math.min(100, p)) + "%"; }

(async function init() { await loadTemplates(); await loadDemos(); await loadWorks(); })();
</script>
</body>
</html>"""


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "8000"))
    host = os.environ.get("HOST", "127.0.0.1")
    print("OpenMontage Render Control Panel")
    print(f"  root:   {ROOT_DIR}")
    print(f"  works:  {WORKS_DIR}")
    print(f"  open:   http://localhost:{port}")
    app.run(host=host, port=port, threaded=True)
