# syntax=docker/dockerfile:1
#
# smeltery Bench: the web app over the smeltery funnel, serving the demo by default (no SCF runs).
#
#   docker build -t smeltery-bench .
#   docker run --rm -p 8765:8765 -v smeltery-data:/data smeltery-bench
#   docker logs <container>        # the sign-in URL, with the token
#
# See docs/bench.md ("Docker") and compose.yaml. Base images are pinned by digest (tag kept for readability);
# bump both together.

# ---- Stage 1: build the UI. src/smeltery/bench/web/ is gitignored build output, so the image builds it itself.
FROM node:22-slim@sha256:e9f565d32859f0cbfb52125eafe8be39390f24dac302dc3cdf566e8ba3ab21ee AS ui
WORKDIR /app/ui
COPY ui/package.json ui/package-lock.json ./
RUN npm ci
COPY ui/ ./
# vite.config.ts writes to ../src/smeltery/bench/web, that is /app/src/smeltery/bench/web
RUN npm run build

# ---- Stage 2: the runtime, with only the `bench` extra (no dev tools, no node).
FROM python:3.12-slim@sha256:5e2bdb1b0d542d5039cffb8181c65930093949194a65c365dfb1b0fe13a4fa5b

# hatch-vcs derives the version from git tags, and .git is not in the build context; pin one instead.
ARG SMELTERY_VERSION=0.0.0+docker
ENV SETUPTOOLS_SCM_PRETEND_VERSION=${SMELTERY_VERSION} \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PYTHONUNBUFFERED=1

WORKDIR /src
COPY pyproject.toml README.md LICENSE-MIT LICENSE-APACHE ./
COPY src/ src/
COPY --from=ui /app/src/smeltery/bench/web/ src/smeltery/bench/web/
# A non-editable install: the wheel carries the built UI and the prebuilt pipelines (hatch `artifacts`).
RUN pip install --root-user-action=ignore ".[bench]" && rm -rf /src

# Non-root. /data is the Bench root and a volume, owned by the app user so a fresh named volume is writable.
RUN useradd --create-home --uid 10001 bench \
    && mkdir /data && chown bench:bench /data
USER bench
WORKDIR /home/bench

COPY --chmod=0755 docker/entrypoint.sh /usr/local/bin/smeltery-bench-entrypoint

ENV SMELTERY_BENCH_ROOT=/data \
    SMELTERY_BENCH_HOST=0.0.0.0 \
    SMELTERY_BENCH_PORT=8765 \
    SMELTERY_BENCH_LOG_JSON=1 \
    SMELTERY_BENCH_STOP_TASKS_ON_EXIT=1
VOLUME /data
EXPOSE 8765

# /healthz is open without the token (a probe must not need the secret) and does not touch the root.
# /readyz also checks the root and the task queue, but it needs the token, so it is for the CI job, not here.
HEALTHCHECK --interval=15s --timeout=5s --start-period=20s --retries=3 \
    CMD ["python", "-c", "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8765/healthz', timeout=4).status == 200 else 1)"]

# exec form, so SIGTERM from `docker stop` reaches uvicorn and Bench's shutdown hook runs.
ENTRYPOINT ["smeltery-bench-entrypoint"]
CMD ["--demo"]
