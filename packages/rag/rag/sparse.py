"""稀疏特征：BM25 风格 TF 加权稀疏向量（固定哈希桶，可与 Milvus SPARSE 对齐）。"""

from __future__ import annotations

import hashlib
import math
import re

SPARSE_DIM = 65536


def tokenize(text: str) -> list[str]:
    """中英混合简单分词：英文按词，中文按字+连续词。"""
    text = text.lower()
    tokens = re.findall(r"[a-z0-9_]+|[\u4e00-\u9fff]", text)
    # 中文 bigram 增强召回
    zh = re.findall(r"[\u4e00-\u9fff]+", text)
    for span in zh:
        for i in range(len(span) - 1):
            tokens.append(span[i : i + 2])
    return tokens


def _hash_token(tok: str, dim: int = SPARSE_DIM) -> int:
    digest = hashlib.blake2b(tok.encode("utf-8"), digest_size=8).digest()
    return int.from_bytes(digest, "big") % dim


def bm25_sparse(
    text: str,
    *,
    dim: int = SPARSE_DIM,
    k1: float = 1.5,
    b: float = 0.75,
    avgdl: float = 256.0,
) -> dict[int, float]:
    """单文档 BM25 TF 分量（无全局 IDF 时用平滑 IDF≈1+log(N/df) 的常数近似）。

    生产可把 document frequency 落 Redis；此处用稳定哈希桶 + 长度归一，
    足以支撑 Hybrid + RRF 的稀疏腿。
    """
    tokens = tokenize(text)
    if not tokens:
        return {}
    tf: dict[str, int] = {}
    for tok in tokens:
        tf[tok] = tf.get(tok, 0) + 1
    dl = float(len(tokens))
    vec: dict[int, float] = {}
    for tok, freq in tf.items():
        # 平滑 IDF：用 token hash 的伪 df 近似，避免所有维权重相同
        pseudo_df = 1 + (_hash_token(tok + ":df", dim) % 1000)
        idf = math.log(1.0 + (10_000.0 - pseudo_df + 0.5) / (pseudo_df + 0.5))
        tf_norm = (freq * (k1 + 1.0)) / (freq + k1 * (1.0 - b + b * dl / avgdl))
        idx = _hash_token(tok, dim)
        vec[idx] = vec.get(idx, 0.0) + float(idf * tf_norm)
    return vec
