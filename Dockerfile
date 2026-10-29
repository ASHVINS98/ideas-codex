# syntax=docker/dockerfile:1
FROM python:3.12-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    DISPLAY=:99 \
    NAUKRI_STATE_DIR=/state \
    NAUKRI_BROWSER=/usr/bin/chromium \
    TZ=Asia/Kolkata

# The optional combined CA bundle is used only for this build step, never copied
# into an image layer. Normal local Docker Desktop builds need no build secrets.
RUN --mount=type=secret,id=proxy_ca \
    set -eu; \
    set --; \
    if [ -s /run/secrets/proxy_ca ]; then \
        set -- -o Acquire::https::CaInfo=/run/secrets/proxy_ca; \
    fi; \
    apt-get "$@" update; \
    apt-get "$@" install -y --no-install-recommends \
      ca-certificates chromium fonts-liberation novnc openbox \
      tini websockify x11vnc xvfb; \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt ./
RUN --mount=type=secret,id=proxy_ca \
    set -eu; \
    if [ -s /run/secrets/proxy_ca ]; then \
        export PIP_CERT=/run/secrets/proxy_ca; \
    fi; \
    python -m pip install --no-cache-dir -r requirements.txt

RUN useradd --create-home --uid 1000 assistant \
    && mkdir -p /state /app/reports \
    && chown -R assistant:assistant /state /app
COPY --chown=assistant:assistant naukri_assistant/ ./naukri_assistant/
COPY --chown=assistant:assistant examples/ ./examples/
COPY --chown=assistant:assistant tests/ ./tests/
COPY --chown=assistant:assistant docker/ ./docker/

USER assistant
EXPOSE 6080
ENTRYPOINT ["/usr/bin/tini", "--", "python", "-m", "naukri_assistant.docker_entrypoint"]
CMD ["desktop"]
