# Deploying the Render Control Panel to the public internet

This gives you a fixed `https://…` URL you can open from any device — no local
setup for visitors. The app is packaged as a single Docker image (see
[`Dockerfile`](./Dockerfile)).

## What you get and what it costs (resources)

- The **page + live preview** is light: the Remotion `<Player>` runs in the
  visitor's browser, so even a tiny instance serves it fine.
- **Rendering** an MP4 spawns headless Chrome + ffmpeg and is memory-heavy.
  Give the instance **≥ 2 GB RAM** for reliable renders, or set
  `REMOTION_CONCURRENCY=1` (slower, but won't get OOM-killed on small hosts).
- Free tiers with 512 MB RAM can show the page and live preview, but full
  renders may be killed. Pick a ~2 GB plan if you need rendering.

> **Remotion license**: Remotion is free for individuals and small companies but
> requires a company license above a size threshold. If you deploy this as a
> business/commercial service, check <https://remotion.dev/license>.

## Prerequisites (one-time)

1. Put your OpenMontage code in **your own** Git repo (Render/Railway/Fly build
   from a repo you own):
   ```bash
   git clone https://github.com/calesthio/OpenMontage.git
   cd OpenMontage
   # drop this openmontage-webui/ folder in at the root (unzip it here)
   cp openmontage-webui/dockerignore.example .dockerignore
   git init && git add . && git commit -m "OpenMontage + render control panel"
   # push to a new GitHub repo you control
   ```
2. The image is built from `openmontage-webui/Dockerfile` with the **OpenMontage
   root as the build context**.

---

## Option A — Try it locally with Docker first (recommended sanity check)

From the OpenMontage root:

```bash
cp openmontage-webui/dockerignore.example .dockerignore
docker build -f openmontage-webui/Dockerfile -t openmontage-webui .
docker run --rm -p 8000:8000 openmontage-webui
# open http://localhost:8000
```

If that works, any Docker host will work the same way.

## Option B — Render.com (Docker, simplest hosted path)

1. Push your repo to GitHub (see Prerequisites).
2. Render → **New → Web Service** → connect the repo.
3. Settings:
   - **Runtime / Environment**: Docker
   - **Dockerfile Path**: `openmontage-webui/Dockerfile`
   - **Docker Build Context Directory**: `.` (the repo root)
   - **Instance Type**: Standard (2 GB) or larger if you'll render
   - **Environment variable** (optional, small instances): `REMOTION_CONCURRENCY=1`
4. Deploy. Render gives you `https://<your-app>.onrender.com`. Render sets `$PORT`
   automatically; the image already binds to it.

## Option C — Railway

1. Push your repo to GitHub.
2. Railway → **New Project → Deploy from GitHub repo**.
3. Settings → **Build**: Dockerfile path `openmontage-webui/Dockerfile`.
4. Add a **public domain** under Settings → Networking. Railway sets `$PORT`.
5. (Small plan) add variable `REMOTION_CONCURRENCY=1`.

## Option D — Fly.io

```bash
cd OpenMontage
cp openmontage-webui/dockerignore.example .dockerignore
fly launch --dockerfile openmontage-webui/Dockerfile --no-deploy   # creates fly.toml
# in fly.toml set internal_port = 8000, and a [vm] with memory ~2048
fly deploy
```

## Option E — Any VPS with Docker

```bash
# on the server, in the OpenMontage root
cp openmontage-webui/dockerignore.example .dockerignore
docker build -f openmontage-webui/Dockerfile -t openmontage-webui .
docker run -d --restart unless-stopped -p 80:8000 \
  -e REMOTION_CONCURRENCY=1 openmontage-webui
```
Then point a domain at the server and put it behind Caddy/Nginx for HTTPS.

---

## Environment variables (all optional)

| Var                   | Use                                                    |
|-----------------------|--------------------------------------------------------|
| `PORT`                | Port to bind (platforms set this automatically)        |
| `REMOTION_CONCURRENCY`| `1` on small hosts to avoid out-of-memory render kills  |
| `OPENMONTAGE_ROOT`    | Already set to `/app` in the image; override if needed |
| `REMOTION_BROWSER_EXECUTABLE` | Path to a preinstalled Chrome (image bakes one in via `remotion browser ensure`) |

## Notes & caveats

- **One render at a time** by design (heavy). Concurrent requests get HTTP 409.
- **Persistence**: rendered MP4s, custom works, and uploads are written inside
  the container and are lost on redeploy unless you mount a volume at
  `/app/projects` and `/app/remotion-composer/public/uploads`.
- **Public exposure**: this build has no authentication — anyone with the URL
  can render and upload. Put it behind your platform's access control / basic
  auth, or a reverse proxy, if it shouldn't be open to the world.
- First render downloads nothing if `remotion browser ensure` succeeded during
  build; otherwise Chrome is fetched on the first render (needs outbound net).
