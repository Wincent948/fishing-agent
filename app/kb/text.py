"""分词与离线向量化。

- tokenize：英文按单词，中文按「单字 + 相邻两字」切，BM25 和离线向量共用。
- HashEmbedder：离线向量模型。把 token 哈希到固定维度并做 TF 加权 + L2 归一化。
  它没有语义理解能力，只为让整条链路在没有 GPU / 网络时可跑；
  设置 EMBEDDER=qwen 后切换为 Qwen3-Embedding-0.6B（需 sentence-transformers 和模型权重）。
"""
from __future__ import annotations

import hashlib
import math
import os
import re

import numpy as np

_CJK = re.compile(r"[一-鿿]+")
_WORD = re.compile(r"[a-zA-Z][a-zA-Z\-]*|\d+(?:\.\d+)?")


def tokenize(text: str) -> list[str]:
    text = text.lower()
    toks = _WORD.findall(text)
    for seg in _CJK.findall(text):
        toks.extend(seg)                                        # 单字
        toks.extend(seg[i:i + 2] for i in range(len(seg) - 1))  # 两字
    return toks


class HashEmbedder:
    name = "hash-512"

    def __init__(self, dim: int = 512):
        self.dim = dim

    def _bucket(self, tok: str) -> int:
        return int(hashlib.md5(tok.encode()).hexdigest(), 16) % self.dim

    def embed(self, texts: list[str]) -> np.ndarray:
        out = np.zeros((len(texts), self.dim), dtype=np.float32)
        for i, t in enumerate(texts):
            for tok in tokenize(t):
                out[i, self._bucket(tok)] += 1.0
            out[i] = np.log1p(out[i])
            n = np.linalg.norm(out[i])
            if n:
                out[i] /= n
        return out


class QwenEmbedder:                                    # pragma: no cover - 需要模型权重
    name = "Qwen3-Embedding-0.6B"

    def __init__(self):
        from sentence_transformers import SentenceTransformer
        self.m = SentenceTransformer("Qwen/Qwen3-Embedding-0.6B")

    def embed(self, texts):
        return np.asarray(self.m.encode(texts, normalize_embeddings=True), dtype=np.float32)


def get_embedder():
    return QwenEmbedder() if os.getenv("EMBEDDER") == "qwen" else HashEmbedder()


def count_tokens(text: str) -> int:
    """粗略 token 计数：中文约 1 字 1 token，英文约 1 词 1.3 token（用于切块和上下文预算）。"""
    cjk = sum(len(s) for s in _CJK.findall(text))
    words = len(_WORD.findall(text))
    return cjk + math.ceil(words * 1.3)
