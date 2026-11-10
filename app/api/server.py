"""FastAPI 业务网关：鉴权（HMAC 签名 token）、按用户限流、请求大小限制、trace_id、统一错误处理。

启动：uvicorn app.api.server:app --reload
  POST /v1/dev-token      仅开发环境：给 user_id 签发 token
  POST /v1/chat           文字提问           {"thread_id": "...", "text": "..."}
  POST /v1/identify       上传图片 + 可选文字（multipart）
  POST /v1/confirm        回答写入确认       {"thread_id": "...", "confirm": true}
  GET/DELETE /v1/memory   查看 / 删除自己的长期记忆
  GET  /healthz
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import os
import time
from collections import defaultdict
from pathlib import Path

from fastapi import Depends, FastAPI, File, Form, Header, HTTPException, UploadFile
from pydantic import BaseModel, Field

from app import config
from app.memory import store
from app.service import run_turn

SECRET = os.getenv("API_SECRET", "dev-secret-change-me").encode()
ENV = os.getenv("APP_ENV", "dev")
RATE_PER_MIN = int(os.getenv("RATE_PER_MIN", "20"))
UPLOADS = config.RUNTIME / "uploads"
UPLOADS.mkdir(exist_ok=True)

app = FastAPI(title="Fishing Agent API", version="0.1.0")


# ---------------- 鉴权：token = base64(user_id).签名；user_id 只从这里来，绝不由模型或请求体提供
def sign(user_id: str) -> str:
    sig = hmac.new(SECRET, user_id.encode(), hashlib.sha256).hexdigest()[:32]
    return f"{base64.urlsafe_b64encode(user_id.encode()).decode()}.{sig}"


def current_user(authorization: str = Header(default="")) -> str:
    try:
        raw, sig = authorization.removeprefix("Bearer ").split(".")
        uid = base64.urlsafe_b64decode(raw.encode()).decode()
    except Exception:  # noqa: BLE001
        raise HTTPException(401, "缺少或无效的 token")
    if not hmac.compare_digest(sign(uid).split(".")[1], sig):
        raise HTTPException(401, "token 签名无效")
    _rate_limit(uid)
    return uid


# ---------------- 限流：每用户每分钟 RATE_PER_MIN 次（单进程内存版；生产换 Redis 令牌桶）
_HITS: dict[str, list[float]] = defaultdict(list)


def _rate_limit(uid: str):
    now = time.time()
    _HITS[uid] = [t for t in _HITS[uid] if now - t < 60] + [now]
    if len(_HITS[uid]) > RATE_PER_MIN:
        raise HTTPException(429, "请求太频繁，请稍后再试")


class ChatIn(BaseModel):
    thread_id: str = Field(min_length=1, max_length=64)
    text: str = Field(max_length=config.MAX_INPUT_CHARS * 2)


class ConfirmIn(BaseModel):
    thread_id: str
    confirm: bool


def _public(r: dict) -> dict:
    """对外只返回必要字段；调试信息只在开发环境返回（生产锁死）。"""
    keep = ["trace_id", "thread_id", "answer", "behavior", "intents", "latency_ms"]
    out = {k: r[k] for k in keep}
    if ENV == "dev":
        out["debug"] = {k: r[k] for k in ("slots", "route_source", "guard_hits", "tool_calls", "trace", "usage")}
    return out


@app.post("/v1/dev-token")
def dev_token(user_id: str):
    if ENV != "dev":
        raise HTTPException(404)
    return {"token": sign(user_id)}


@app.post("/v1/chat")
def chat(body: ChatIn, uid: str = Depends(current_user)):
    return _public(run_turn(uid, f"{uid}:{body.thread_id}", text=body.text))


@app.post("/v1/identify")
async def identify(thread_id: str = Form(...), text: str = Form("这是什么鱼？"), image: UploadFile = File(...),
                   uid: str = Depends(current_user)):
    data = await image.read()
    if len(data) > config.MAX_IMAGE_MB * 2**20:
        raise HTTPException(413, f"图片超过 {config.MAX_IMAGE_MB} MB")
    path = UPLOADS / f"{uid}-{int(time.time())}-{Path(image.filename or 'img.jpg').name}"
    path.write_bytes(data)
    return _public(run_turn(uid, f"{uid}:{thread_id}", text=text, image=str(path)))


@app.post("/v1/confirm")
def confirm(body: ConfirmIn, uid: str = Depends(current_user)):
    return _public(run_turn(uid, f"{uid}:{body.thread_id}", resume={"confirm": body.confirm}))


@app.get("/v1/memory")
def get_memory(uid: str = Depends(current_user)):
    return {"profile": store.get_profile(uid), "recent_catches": store.list_catches(uid)}


@app.delete("/v1/memory")
def delete_memory(uid: str = Depends(current_user)):
    return {"deleted": store.delete_profile(uid)}


@app.get("/healthz")
def healthz():
    kb_ok = (config.RUNTIME / "kb" / "manifest.json").exists()
    return {"ok": True, "kb_index": kb_ok, "offline_mode": config.OFFLINE, "fixture_mode": config.FIXTURE_MODE}
