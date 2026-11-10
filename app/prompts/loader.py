"""加载带版本号的提示词 YAML → LangChain ChatPromptTemplate。trace 里记录用了哪个版本。"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

import yaml

DIR = Path(__file__).resolve().parent


@lru_cache(maxsize=None)
def load(name: str) -> dict:
    return yaml.safe_load((DIR / f"{name}.yaml").read_text(encoding="utf-8"))


def template(name: str):
    from langchain_core.prompts import ChatPromptTemplate
    p = load(name)
    return ChatPromptTemplate.from_messages([("system", p["system"]), ("placeholder", "{context}"), ("human", "{question}")])


def version(name: str) -> str:
    return f"{name}@v{load(name)['version']}"
