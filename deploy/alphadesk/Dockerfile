# AlphaDesk dashboard backend (API only, no bundled frontend), with the Kronos forecast model on CPU.
# Build context is the repo root with third_party/Kronos present (it is not tracked in git; rsync it in).
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1

RUN apt-get update && apt-get install -y --no-install-recommends build-essential \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY agent/requirements.txt agent/requirements.txt
RUN pip install -r agent/requirements.txt \
    && pip install torch --index-url https://download.pytorch.org/whl/cpu \
    && pip install einops safetensors huggingface_hub tqdm

COPY agent/ agent/
COPY third_party/Kronos third_party/Kronos

RUN useradd --create-home --shell /usr/sbin/nologin vibe \
    && mkdir -p /home/vibe/.vibe-trading && chown -R vibe:vibe /app /home/vibe
USER vibe
WORKDIR /app/agent
ENV HOME=/home/vibe PYTHONPATH=/app/agent

EXPOSE 8899
HEALTHCHECK --interval=30s --timeout=5s --start-period=40s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8899/live')" || exit 1
CMD ["python", "-c", "import api_server; raise SystemExit(api_server.serve_main(['--host','0.0.0.0','--port','8899']))"]
