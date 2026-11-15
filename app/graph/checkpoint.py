"""短期记忆（checkpointer）的选择：设置了 REDIS_URL 用 Redis，否则用进程内存（原有行为）。

Redis 版的好处：API 重启后，进行到一半的对话、等待确认的写入（interrupt）都能恢复；多个 API 实例共享同一份对话状态。
不设 REDIS_URL 时行为与原来完全一致（本地开发、单测、评测、魔搭演示都不受影响）。
"""
from __future__ import annotations

import logging
import os

from langgraph.checkpoint.memory import MemorySaver

log = logging.getLogger(__name__)

REDIS_URL = os.getenv("REDIS_URL", "")
CHECKPOINT_TTL_MIN = int(os.getenv("CHECKPOINT_TTL_MIN", str(7 * 24 * 60)))   # 对话状态保留 7 天，到期自动清理


def make_checkpointer():
    if not REDIS_URL:
        return MemorySaver()
    from langgraph.checkpoint.redis import RedisSaver
    saver = RedisSaver(redis_url=REDIS_URL, ttl={"default_ttl": CHECKPOINT_TTL_MIN, "refresh_on_read": True})
    saver.setup()                      # 首次运行时创建索引；已存在则跳过
    log.info("checkpointer: Redis (%s)", REDIS_URL.split("@")[-1])
    return saver


def checkpointer_kind(saver) -> str:
    return "redis" if type(saver).__name__ == "RedisSaver" else "memory"
