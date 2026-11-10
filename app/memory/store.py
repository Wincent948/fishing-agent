"""长期记忆与渔获日志：SQLite，所有读写都按 user_id 隔离（行级）。

- profile：用户画像（常去地点、语言、偏好），对应 LangGraph Store 的 (user_id, "profile") 命名空间
- catches：情景记忆 / 渔获日志
短期会话记忆由 LangGraph checkpointer 负责（见 app/graph/main_graph.py）。
"""
from __future__ import annotations

import json
import sqlite3
import threading

from app import config

_DB = config.RUNTIME / "memory.sqlite"
_LOCK = threading.Lock()


def _conn():
    c = sqlite3.connect(_DB, check_same_thread=False)
    c.execute("CREATE TABLE IF NOT EXISTS profile(user_id TEXT, key TEXT, value TEXT, updated TEXT, PRIMARY KEY(user_id,key))")
    c.execute("CREATE TABLE IF NOT EXISTS catches(id INTEGER PRIMARY KEY, user_id TEXT, date TEXT, species TEXT,"
              " count INTEGER, length_cm REAL, location TEXT, idem_key TEXT UNIQUE)")
    return c


def reset(seed: bool = True):
    """评测前调用：清空并写入 data/synthetic/users.json 里的种子用户。"""
    with _LOCK:
        c = _conn()
        c.execute("DELETE FROM profile")
        c.execute("DELETE FROM catches")
        if seed:
            users = json.loads((config.DATA / "users.json").read_text(encoding="utf-8"))
            for uid, u in users.items():
                for k, v in u["profile"].items():
                    c.execute("INSERT INTO profile VALUES(?,?,?,?)", (uid, k, v, "seed"))
                for i, x in enumerate(u["catches"]):
                    c.execute("INSERT INTO catches(user_id,date,species,count,length_cm,location,idem_key)"
                              " VALUES(?,?,?,?,?,?,?)", (uid, x["date"], x["species"], x["count"], x["length_cm"],
                                                         x["location"], f"seed-{uid}-{i}"))
        c.commit()


def get_profile(user_id: str) -> dict:
    return {k: v for k, v in _conn().execute("SELECT key,value FROM profile WHERE user_id=?", (user_id,))}


def set_profile(user_id: str, key: str, value: str, when: str):
    with _LOCK:
        c = _conn()
        c.execute("INSERT OR REPLACE INTO profile VALUES(?,?,?,?)", (user_id, key, value, when))
        c.commit()


def delete_profile(user_id: str, key: str | None = None) -> int:
    with _LOCK:
        c = _conn()
        cur = c.execute("DELETE FROM profile WHERE user_id=?" + (" AND key=?" if key else ""),
                        (user_id, key) if key else (user_id,))
        c.commit()
        return cur.rowcount


def add_catch(user_id: str, row: dict, idem_key: str) -> bool:
    with _LOCK:
        c = _conn()
        try:
            c.execute("INSERT INTO catches(user_id,date,species,count,length_cm,location,idem_key) VALUES(?,?,?,?,?,?,?)",
                      (user_id, row["date"], row["species"], row["count"], row.get("length_cm"), row.get("location"), idem_key))
            c.commit()
            return True
        except sqlite3.IntegrityError:            # 幂等键重复：同一次确认不会写两遍
            return False


def list_catches(user_id: str, limit: int = 5) -> list[dict]:
    cols = ["date", "species", "count", "length_cm", "location"]
    rows = _conn().execute(f"SELECT {','.join(cols)} FROM catches WHERE user_id=? ORDER BY id DESC LIMIT ?",
                           (user_id, limit)).fetchall()
    return [dict(zip(cols, r)) for r in rows]
