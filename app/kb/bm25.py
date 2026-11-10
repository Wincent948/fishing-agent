"""BM25：关键词检索（手写实现，方便理解公式）。

score(q, d) = Σ_t IDF(t) · f(t,d)·(k1+1) / (f(t,d) + k1·(1 - b + b·|d|/avgdl))
越稀有的词 IDF 越大；词频有饱和（k1 控制）；长文档会被惩罚（b 控制）。
"""
from __future__ import annotations

import math
from collections import Counter

from app.kb.text import tokenize


class BM25:
    def __init__(self, docs: list[str], k1: float = 1.5, b: float = 0.75):
        self.k1, self.b = k1, b
        self.tfs = [Counter(tokenize(d)) for d in docs]
        self.lens = [sum(tf.values()) for tf in self.tfs]
        self.avgdl = sum(self.lens) / max(1, len(self.lens))
        df = Counter(t for tf in self.tfs for t in tf)
        n = len(docs)
        self.idf = {t: math.log(1 + (n - c + 0.5) / (c + 0.5)) for t, c in df.items()}

    def scores(self, query: str, allowed: set[int] | None = None) -> list[tuple[int, float]]:
        q = tokenize(query)
        res = []
        for i, tf in enumerate(self.tfs):
            if allowed is not None and i not in allowed:
                continue
            s = 0.0
            for t in q:
                if t in tf:
                    f = tf[t]
                    s += self.idf[t] * f * (self.k1 + 1) / (f + self.k1 * (1 - self.b + self.b * self.lens[i] / self.avgdl))
            if s > 0:
                res.append((i, s))
        return sorted(res, key=lambda x: -x[1])
