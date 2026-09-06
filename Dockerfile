# ── Build stage ──────────────────────────────────
FROM python:3.11-slim AS builder

RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        gcc \
        libc6-dev \
        libffi-dev \
        make \
        cmake \
        python3 \
        git && \
    rm -rf /var/lib/apt/lists/*

# Build QuickJS-NG
RUN cd /tmp && \
    git clone --depth 1 https://github.com/quickjs-ng/quickjs.git && \
    cd quickjs && \
    make -j$(nproc) && \
    make install && \
    rm -rf /tmp/quickjs

WORKDIR /build
COPY requirements.txt .
RUN pip install --no-cache-dir --prefix=/install -r requirements.txt

# ── Final stage ──────────────────────────────────
FROM python:3.11-slim

# Install ffmpeg and runtime deps
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        ffmpeg \
        curl && \
    rm -rf /var/lib/apt/lists/*

# Copy QuickJS-NG from builder
COPY --from=builder /usr/local/bin/qjs /usr/local/bin/qjs

WORKDIR /app

# Copy installed packages from builder
COPY --from=builder /install /usr/local

# Copy application code
COPY bot.py config.py ./
COPY app/ ./app/

# Create data directories
RUN mkdir -p data downloads ssl

# Remove pip to save space
RUN pip uninstall -y pip setuptools wheel 2>/dev/null; true

EXPOSE 8000

CMD ["python", "bot.py"]
