FROM python:3.14.7-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    HOST=0.0.0.0 \
    PORT=8000 \
    DOCUMENTS_DIR=/data/documents \
    FASTEMBED_CACHE_PATH=/opt/fastembed

WORKDIR /app
# Dependencies and the search model come first so code changes reuse these layers.
# The model name must match MODEL_NAME in noteworthy_app/search.py.
COPY requirements.txt ./
RUN python -m pip install --no-cache-dir -r requirements.txt \
    && python -c "from fastembed import TextEmbedding; TextEmbedding('sentence-transformers/all-MiniLM-L6-v2')"
# The model is in the image, so the app never downloads it at runtime.
ENV HF_HUB_OFFLINE=1
COPY pyproject.toml README.md ./
COPY noteworthy_app/ ./noteworthy_app/
RUN python -m pip install --no-cache-dir --no-deps . \
    && groupadd --gid 10001 app \
    && useradd --uid 10001 --gid app --no-create-home --shell /usr/sbin/nologin app \
    && mkdir -p /data/documents \
    && chown app:app /data/documents \
    && chown -R app:app /opt/fastembed

USER app
# Fail the build if the app user cannot load the search model offline.
RUN python -c "from noteworthy_app.search import fastembed_embedder; fastembed_embedder()(['check'])"
EXPOSE 8000
HEALTHCHECK --interval=10s --timeout=3s --start-period=5s --retries=3 \
    CMD python -c "import os, urllib.request; urllib.request.urlopen('http://127.0.0.1:' + os.environ.get('PORT', '8000') + '/health', timeout=2).close()"
CMD ["noteworthy-app"]
