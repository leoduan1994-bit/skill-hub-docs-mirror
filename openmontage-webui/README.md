# OpenMontage — Render Control Panel (web UI)

A small, self-contained web page for operating OpenMontage's **zero-key Remotion
demos** directly from the browser: pick a demo, optionally tweak its props JSON,
click **Render**, then preview and download the resulting MP4. **No API keys
required** — these demos render from local Remotion components.

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

## What you can do

- **Render a demo** — `world-in-numbers`, `code-to-screen`, `focusflow-pitch`.
- **Edit props** — expand *Advanced* to edit the demo's props JSON (titles,
  stats, timings, colors) before rendering. Must keep a non-empty `cuts` array.
- **Preview & download** — the finished MP4 plays inline and downloads from
  `projects/demos/renders/<demo>.mp4`.

## Notes

- One render runs at a time (Remotion rendering is CPU-heavy); a second request
  while busy returns HTTP 409.
- Edited props are saved to `projects/demos/renders/_webui_props/<demo>.json`.
- Outputs land in `projects/demos/renders/` — the same path as `make demo`.
