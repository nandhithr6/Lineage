# API + loader image. The snapshot (JSONL, a few MB) is baked in; raw clones are not needed at runtime.
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 UV_LINK_MODE=copy UV_COMPILE_BYTECODE=1
RUN pip install --no-cache-dir uv
WORKDIR /app
COPY pyproject.toml uv.lock .python-version ./
RUN uv sync --frozen --no-dev
ENV PATH="/app/.venv/bin:$PATH" FASTEMBED_CACHE=/opt/fastembed
COPY lineage ./lineage
COPY snapshot ./snapshot
# local embedding model for search_text (about 210 MB), baked in so the container needs no network at runtime
RUN python -c "from fastembed import TextEmbedding; TextEmbedding('BAAI/bge-base-en-v1.5', cache_dir='/opt/fastembed')" && chmod -R a+rX /opt/fastembed

RUN useradd --create-home --uid 10001 lineage
USER lineage

EXPOSE 8000
HEALTHCHECK --interval=15s --timeout=3s --retries=5 CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://localhost:8000/health', timeout=2).status == 200 else 1)"
CMD ["uvicorn", "lineage.api.main:app", "--host", "0.0.0.0", "--port", "8000"]
