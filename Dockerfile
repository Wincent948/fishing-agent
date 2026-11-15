# Fishing Agent API：FastAPI + LangGraph + ChromaDB
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PORT=8000

WORKDIR /app

# 先装依赖，代码改动时可复用这一层缓存
COPY requirements.txt .
RUN pip install -r requirements.txt

COPY . .

# 非 root 运行；.runtime（Chroma 索引、SQLite 记忆、上传图片）挂数据卷持久化
RUN useradd --create-home --uid 1000 app \
    && mkdir -p /app/.runtime \
    && chown -R app:app /app/.runtime
USER app

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=60s --retries=3 \
    CMD python -c "import os,urllib.request; urllib.request.urlopen(f'http://127.0.0.1:{os.environ.get(\"PORT\",\"8000\")}/health', timeout=4)" || exit 1

ENTRYPOINT ["./docker/entrypoint.sh"]
