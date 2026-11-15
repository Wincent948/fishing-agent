#!/bin/sh
# 容器启动：索引不在数据卷里就先建一次（首次启动约十几秒），再起 API。
set -e
if [ ! -f .runtime/kb/manifest.json ]; then
  echo "[entrypoint] 未发现知识库索引，开始建库 …"
  python -m app.kb.index
fi
exec uvicorn app.api.server:app --host 0.0.0.0 --port "${PORT:-8000}"
