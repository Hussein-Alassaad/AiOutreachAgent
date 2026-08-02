# Build from the REPO ROOT, not from agent/, so `agent` lands in the image as
# an importable package (every module here does `from agent import ...`):
#
#   docker build -f agent/Dockerfile -t nexaris-agent .
#
# See agent/DEPLOY.md for the full VPS setup this feeds into.

FROM python:3.11-slim

WORKDIR /app

# wget/gnupg/ca-certificates are what Playwright's own install step needs to
# pull down and verify the Chromium build; --with-deps below adds the rest
# (fonts, codecs, etc.) that headless Chromium needs on a bare slim image.
RUN apt-get update && apt-get install -y --no-install-recommends \
    wget gnupg ca-certificates \
    && rm -rf /var/lib/apt/lists/*

COPY agent/requirements.txt agent/requirements.txt
RUN pip install --no-cache-dir -r agent/requirements.txt \
    && playwright install --with-deps chromium

COPY agent agent

# Unbuffered stdout so `docker logs` shows scheduler output as it happens,
# not only once Python's buffer flushes.
ENV PYTHONUNBUFFERED=1

# agent/.env is NOT copied in -- real credentials are passed as container env
# vars at deploy time (see DEPLOY.md), never baked into the image.

CMD ["python", "-m", "agent.server"]
