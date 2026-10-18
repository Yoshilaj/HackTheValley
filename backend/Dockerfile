FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PLAYWRIGHT_BROWSERS_PATH=/ms-playwright \
    FASTEMBED_CACHE_PATH=/opt/fastembed

# libsndfile for soundfile/librosa; ffmpeg to decode browser audio (webm/m4a).
RUN apt-get update \
    && apt-get install -y --no-install-recommends libsndfile1 ffmpeg \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt

# Bake the local RAG models into the image (FASTEMBED_CACHE_PATH) so the first request does not
# download ~130 MB and a cold start works offline. The quantized embedder is always included.
# The cross-encoder reranker (~80 MB on disk, +140 MB RSS) is only useful with RERANKER=cross_encoder;
# the Render default is RERANKER=identity, so it is skipped unless --build-arg WITH_RERANKER=1.
ARG EMBEDDING_MODEL=nomic-ai/nomic-embed-text-v1.5-Q
ARG WITH_RERANKER=0
ARG RERANKER_MODEL=Xenova/ms-marco-MiniLM-L-6-v2
RUN python -c "from fastembed import TextEmbedding; \
m = TextEmbedding(model_name='${EMBEDDING_MODEL}'); print('embedder', len(list(m.embed(['warm-up']))[0]))" \
    && if [ "$WITH_RERANKER" = "1" ]; then \
        python -c "from fastembed.rerank.cross_encoder import TextCrossEncoder; \
TextCrossEncoder(model_name='${RERANKER_MODEL}'); print('reranker ok')"; \
    fi \
    && rm -rf /root/.cache

# Website booking channel (app/services/booking/channels/web.py). The playwright Python package is in
# requirements.txt; this step adds the Chromium headless shell only (--only-shell, ~+250 MB image).
# Memory trade-off: one booking run peaks at ~340 MB (single-process Chromium + node driver,
# WEB_BOOKING_LOW_MEMORY=true default) on top of the API (~120 MB) and the local embedder (~285 MB),
# which does not fit Render's 512 MB free tier. Default WEB_BOOKING=0 for the Render image (bookings
# go by e-mail / phone, WEB_BOOKING_ENABLED=false); build with --build-arg WEB_BOOKING=1 on a >= 1 GB
# instance or locally to enable it.
ARG WEB_BOOKING=0
RUN if [ "$WEB_BOOKING" = "1" ]; then \
        python -m playwright install --with-deps --only-shell chromium \
        && rm -rf /var/lib/apt/lists/* /root/.cache; \
    fi

COPY . .
RUN mkdir -p data/uploads

EXPOSE 8000
# Exactly 1 worker so the in-process APScheduler runs once.
# --proxy-headers / --forwarded-allow-ips: Render's proxy is the only way in, so trust its
# X-Forwarded-For; without it every request looks like one client and the per-IP rate limits on
# /api/auth would lock everyone out together.
# --timeout-keep-alive 75: longer than the proxy's idle timeout, so the proxy never reuses a
# connection uvicorn has just closed (that race shows up as an instant 502).
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000} --workers 1 --timeout-keep-alive 75 --proxy-headers --forwarded-allow-ips='*'"]
