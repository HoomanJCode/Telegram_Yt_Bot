# ── Build stage ──────────────────────────────────
FROM python:3.11-slim AS builder

# Build tools needed only to compile QuickJS-NG and download/extract the
# static ffmpeg binaries. None of this lands in the final image.
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        gcc \
        libc6-dev \
        libffi-dev \
        make \
        cmake \
        python3 \
        git \
        curl \
        xz-utils && \
    rm -rf /var/lib/apt/lists/*

# Build QuickJS-NG
RUN cd /tmp && \
    git clone --depth 1 https://github.com/quickjs-ng/quickjs.git && \
    cd quickjs && \
    make -j$(nproc) && \
    make install && \
    rm -rf /tmp/quickjs

# Static ffmpeg + ffprobe (self-contained binaries, all codecs built in).
# Replaces the apt `ffmpeg` package, which drags in the entire libav* +
# codec dependency tree (~250-350 MB). The static tarball is ~39 MB and
# the binaries have zero runtime apt deps, shrinking the final image by
# several hundred MB. Override with --build-arg FFMPEG_VERSION / FFMPEG_ARCH
# (e.g. arm64) when needed.
ARG FFMPEG_VERSION=7.0.2
ARG FFMPEG_ARCH=amd64
RUN curl -fsSL -o /tmp/ffmpeg.tar.xz \
        "https://johnvansickle.com/ffmpeg/releases/ffmpeg-${FFMPEG_VERSION}-${FFMPEG_ARCH}-static.tar.xz" && \
    tar -xJf /tmp/ffmpeg.tar.xz -C /tmp && \
    cp /tmp/ffmpeg-${FFMPEG_VERSION}-${FFMPEG_ARCH}-static/ffmpeg /usr/local/bin/ffmpeg && \
    cp /tmp/ffmpeg-${FFMPEG_VERSION}-${FFMPEG_ARCH}-static/ffprobe /usr/local/bin/ffprobe && \
    chmod +x /usr/local/bin/ffmpeg /usr/local/bin/ffprobe && \
    rm -rf /tmp/ffmpeg.tar.xz /tmp/ffmpeg-${FFMPEG_VERSION}-${FFMPEG_ARCH}-static

WORKDIR /build
COPY requirements.txt .
RUN pip install --no-cache-dir --no-compile --prefix=/install -r requirements.txt

# Remove unnecessary files from installed packages
RUN find /install -name "__pycache__" -type d -exec rm -rf {} + 2>/dev/null; \
    find /install -name "*.pyc" -delete 2>/dev/null; \
    find /install -name "*.pyo" -delete 2>/dev/null; \
    find /install -name "tests" -type d -exec rm -rf {} + 2>/dev/null; \
    find /install -name "test" -type d -exec rm -rf {} + 2>/dev/null; \
    find /install -name "*.dist-info" -type d -exec rm -rf {} + 2>/dev/null; \
    rm -rf /install/lib/python*/site-packages/*/tests/ 2>/dev/null; \
    rm -rf /install/lib/python*/site-packages/*/test/ 2>/dev/null; \
    rm -rf /install/lib/python*/site-packages/setuptools/ 2>/dev/null; \
    rm -rf /install/lib/python*/site-packages/pip/ 2>/dev/null; \
    rm -rf /install/lib/python*/site-packages/wheel/ 2>/dev/null

# ── Final stage ──────────────────────────────────
FROM python:3.11-slim

# No apt installs here: the static binaries are copied straight in, so
# the final image stays slim (no ffmpeg codec libs, no curl).
COPY --from=builder /usr/local/bin/qjs /usr/local/bin/qjs
COPY --from=builder /usr/local/bin/ffmpeg /usr/local/bin/ffmpeg
COPY --from=builder /usr/local/bin/ffprobe /usr/local/bin/ffprobe

WORKDIR /app

# Copy installed packages from builder
COPY --from=builder /install /usr/local

# Copy application code
COPY bot.py config.py ./
COPY app/ ./app/

# Create data directories
RUN mkdir -p data downloads ssl

# Final cleanup
RUN pip uninstall -y pip setuptools wheel 2>/dev/null; true

EXPOSE 8000

CMD ["python", "bot.py"]