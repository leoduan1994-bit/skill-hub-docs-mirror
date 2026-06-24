# OpenMontage Render Control Panel — deployable image.
#
# Build CONTEXT must be the OpenMontage checkout root (this app needs the whole
# repo: remotion-composer/, render_demo.py, etc.). From the OpenMontage root:
#
#   docker build -f openmontage-webui/Dockerfile -t openmontage-webui .
#   docker run -p 8000:8000 openmontage-webui
#   # open http://localhost:8000
#
# On a host (Render/Railway/Fly/VPS) the platform sets $PORT; this image honors it.
#
# Note: rendering spawns headless Chrome + ffmpeg and is memory-heavy. Give the
# instance >= 2 GB RAM for reliable renders, or set REMOTION_CONCURRENCY=1 to
# trade speed for lower memory. The live <Player> preview runs in the visitor's
# browser, so the page itself is light on the server.

FROM node:20-bookworm-slim

# --- System deps: Python, ffmpeg, and the shared libraries headless Chrome needs ---
RUN apt-get update && apt-get install -y --no-install-recommends \
      python3 python3-pip ffmpeg ca-certificates fonts-liberation \
      libnss3 libdbus-1-3 libatk1.0-0 libatk-bridge2.0-0 libcups2 libdrm2 \
      libxkbcommon0 libxcomposite1 libxdamage1 libxfixes3 libxrandr2 libgbm1 \
      libpango-1.0-0 libcairo2 libasound2 libatspi2.0-0 libgtk-3-0 \
      libx11-6 libxcb1 libxext6 libxi6 libxtst6 libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY . /app

# --- Python deps (Flask + gunicorn) ---
RUN pip3 install --no-cache-dir --break-system-packages \
      -r requirements.txt flask gunicorn

# --- Remotion composer deps + headless Chrome baked into the image ---
RUN cd remotion-composer && npm install --no-fund --no-audit \
    && npx remotion browser ensure \
    || echo "WARN: 'remotion browser ensure' failed at build time — Chrome will be fetched on first render instead."

# --- Pre-build the live-preview bundle so the first preview is instant (best-effort) ---
RUN cd /app/openmontage-webui \
    && python3 -c "import app; b,e=app.build_preview_bundle(); print('preview bundle:', b or e)" \
    || echo "WARN: preview bundle will build on first request instead."

ENV OPENMONTAGE_ROOT=/app
ENV PORT=8000
EXPOSE 8000
WORKDIR /app/openmontage-webui

# Single worker + threads: render jobs are tracked in-memory and limited to one
# at a time, so the process must not be forked across workers.
CMD ["sh", "-c", "gunicorn -w 1 --threads 8 --timeout 0 -b 0.0.0.0:${PORT:-8000} app:app"]
