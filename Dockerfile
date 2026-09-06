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

# Install only essential ffmpeg libs (not the full package)
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        ffmpeg \
        curl && \
    # Remove ffmpeg docs, man pages, and examples
    rm -rf /usr/share/doc/ffmpeg /usr/share/man /usr/share/ffmpeg && \
    # Remove apt cache
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

# Final cleanup
RUN pip uninstall -y pip setuptools wheel 2>/dev/null; true

EXPOSE 8000

CMD ["python", "bot.py"]
