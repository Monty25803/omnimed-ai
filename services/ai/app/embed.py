"""Deterministic local text embedding for pgvector retrieval.

Feature hashing over unigrams and bigrams into 256 dimensions, L2-normalised.
It needs no model download; a sentence-transformer can replace it without
changing the vector column if the dimension is kept.
"""
from __future__ import annotations

import hashlib
import math
import re

DIM = 256
_stop = {"the", "a", "an", "and", "or", "of", "to", "in", "with", "for", "on", "is", "are", "be", "as", "by", "at", "if", "use", "give"}


def _h(s: str) -> tuple[int, float]:
    d = hashlib.blake2b(s.encode(), digest_size=8).digest()
    return int.from_bytes(d[:4], "little") % DIM, 1.0 if d[4] & 1 else -1.0


def embed(text: str) -> list[float]:
    words = [w for w in re.findall(r"[a-z0-9]+", text.lower()) if w not in _stop]
    v = [0.0] * DIM
    for w in words:
        i, s = _h(w)
        v[i] += s
    for a, b in zip(words, words[1:]):
        i, s = _h(a + " " + b)
        v[i] += 0.5 * s
    n = math.sqrt(sum(x * x for x in v)) or 1.0
    return [x / n for x in v]


def to_pgvector(v: list[float]) -> str:
    return "[" + ",".join(f"{x:.6f}" for x in v) + "]"
