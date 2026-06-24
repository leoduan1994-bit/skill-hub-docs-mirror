# OpenMontage — Render Control Panel (web UI)

A small, self-contained web page for operating OpenMontage's **Explainer**
video composition directly from the browser — **no API keys required**, all
rendering is from local Remotion components. Two tabs:

- **Demos** — render the checked-in zero-key demos; optionally tweak their props
  JSON before rendering.
- **My Works** — build your **own** videos from scratch: create a work from a
  starter template, add scenes from a snippet palette, switch theme, edit the
  props JSON, **save**, **render**, preview, download — and **delete** when done.

Plus, on both tabs:

- **Live preview** (no render) — play the current props in-browser via the
  Remotion `<Player>`. The composition is bundled once with esbuild and updates
  each time you click *Live preview*.
- **Asset uploads** — upload audio / images / video and use them as a scene,
  background, narration, or music. Files are stored in the composition's
  `public/uploads/` so they work in both live preview and final render.

This is a thin control panel over the same render path as `render_demo.py`
(`npx remotion render … --codec h264`). It does **not** run the full
natural-language → video agent pipeline (that layer is driven by an AI coding
assistant in OpenMontage by design).

## Requirements

- A checked-out **OpenMontage** repo with its setup done:
  - `pip install -r requirements.txt`
  - `cd remotion-composer && npm install`
  - `ffmpeg` on PATH, Node.js 18+
- Python 3.10+ and `flask` (`pip install flask`)

## Install & run

Place this `openmontage-webui/` folder at the **root of your OpenMontage
checkout**, so the layout is `OpenMontage/openmontage-webui/app.py`. Then:

```bash
cd OpenMontage
pip install flask                      # or: pip install -r openmontage-webui/requirements.txt
python openmontage-webui/app.py
```

Open <http://localhost:8000>.

> First render only: Remotion may download a headless-Chrome shell via `npx`
> (one-time, needs network). Subsequent renders are offline.

### Options (environment variables)

| Var               | Default     | Purpose                                            |
|-------------------|-------------|----------------------------------------------------|
| `PORT`            | `8000`      | Port to serve on                                   |
| `HOST`            | `127.0.0.1` | Bind address (use `0.0.0.0` to expose on your LAN) |
| `OPENMONTAGE_ROOT`| auto-detect | Point at an OpenMontage checkout explicitly        |
| `REMOTION_BROWSER_EXECUTABLE` | unset | Use an existing Chrome/Chromium instead of letting Remotion download its headless shell (useful offline or behind a strict network policy) |

If the UI can't find your checkout, it auto-detects by walking up from this
folder looking for `remotion-composer/`; override with `OPENMONTAGE_ROOT`.

## Demos tab

- **Render a demo** — `world-in-numbers`, `code-to-screen`, `focusflow-pitch`.
- **Edit props** — expand *Advanced* to edit the demo's props JSON (titles,
  stats, timings, colors) before rendering. Must keep a non-empty `cuts` array.
- **Preview & download** — the finished MP4 plays inline.

## My Works tab — build your own videos

1. **Create** a work: give it a name and pick a **starter template**
   (`blank`, `explainer`, `data-story`, `product-pitch`). It's saved as a props
   file under `projects/custom-works/<name>.json`.
2. **Add scenes** from the *Insert scene* palette — each click appends a ready
   scene to the `cuts` array, auto-timed to start where the previous one ends.
3. **Switch theme** (`flat-motion-graphics`, `clean-professional`,
   `minimalist-diagram`, `anime-ghibli`) — sets `props.theme`.
4. **Edit the props JSON** directly for fine control; the footer shows scene
   count / duration / theme and warns if the JSON is invalid.
5. **Save**, then **Render** → preview and download.
6. **Delete** removes the work's props file.

### Scene snippets available

`hero_title`, `text_card`, `callout`, `stat_card`, `comparison`, `bar_chart`,
`line_chart`, `pie_chart`, `kpi_grid`, `progress_bar`, `terminal_scene` — every
snippet is valid against the Explainer composition's schema, so anything you
assemble renders. (The composition also supports image/video/`anime_scene`/
`screenshot_scene` scenes when you supply asset paths in `source`.)

Composition duration is derived automatically from the last cut's
`out_seconds` (+1s), so works can be any length.

### Live preview (Remotion Player)

Click **👁 Live preview** on either tab to play the current props in your
browser — no render, no waiting. On first use the app bundles the `Explainer`
composition + `@remotion/player` with the esbuild that ships in
`remotion-composer/node_modules` (cached to
`projects/demos/renders/_webui_preview/bundle.js`; force a rebuild with
`/preview.bundle.js?rebuild=1`). If esbuild isn't found, run
`cd remotion-composer && npm install`.

### Uploading assets (audio / images / video)

Open **Assets** in the *My Works* editor:

- **Upload** an image, video, or audio file → saved to
  `remotion-composer/public/uploads/<file>`.
- For an image/video: **Add as scene** appends a media cut using it as `source`.
- For audio: **Set music** / **Set narration** wires it into `audio.music.src` /
  `audio.narration.src`.
- **Copy** grabs the `uploads/<file>` path to paste anywhere in the props (e.g.
  a cut's `backgroundImage` / `backgroundVideo`).

Because uploads live under the composition's `public/`, the same path resolves
in both the live preview and the final `npx remotion render`.

## API (for scripting)

| Method & path            | Purpose                                  |
|--------------------------|------------------------------------------|
| `GET /api/demos`         | List built-in demos                      |
| `GET /api/templates`     | Themes, scene snippets, starters         |
| `GET/POST /api/works`    | List / create custom works               |
| `GET/PUT/DELETE /api/works/<name>` | Read / save / delete a work    |
| `POST /api/render`       | `{kind:"demo"\|"work", name, props?}`    |
| `GET /api/jobs/<id>`     | Render status, log, progress             |
| `GET /preview.bundle.js` | Browser bundle for the live `<Player>`   |
| `GET/POST /api/assets` · `DELETE /api/assets/<f>` | List / upload / delete assets |
| `GET /uploads/<f>`       | Serve an uploaded asset (for preview)    |
| `GET /video/<f>` · `GET /download/<f>` | Stream (Range) / download MP4 |

## Notes

- One render runs at a time (Remotion rendering is CPU-heavy); a second request
  while busy returns HTTP 409.
- Demo outputs land in `projects/demos/renders/<demo>.mp4`; work outputs in
  `projects/demos/renders/work-<name>.mp4`.
- Render-time edits (not yet saved) go to
  `projects/demos/renders/_webui_props/`.
