"""纯函数单测：不依赖外部 LLM / MQ / Milvus。"""

from rag.ingest import parent_child_chunk, recursive_split
from rag.models import ParsedDocument, RetrievalHit
from rag.retrieve.service import reciprocal_rank_fusion


def test_recursive_split_basic():
    text = "第一段。\n\n" + ("内容" * 200)
    parts = recursive_split(text, size=80, overlap=10)
    assert len(parts) >= 2
    assert all(isinstance(p, str) for p in parts)


def test_parent_child_chunk():
    parsed = ParsedDocument(doc_id="d1", filename="a.md", text="标题\n\n" + ("段落内容。" * 50))
    chunks = parent_child_chunk(parsed, child_size=60, child_overlap=10, parent_size=120)
    kinds = {c.metadata.get("kind") for c in chunks}
    assert "parent" in kinds and "child" in kinds
    children = [c for c in chunks if c.metadata.get("kind") == "child"]
    assert all(c.parent_id for c in children)


def test_rrf_fusion_order():
    a = [
        RetrievalHit(chunk_id="1", doc_id="d", text="a", score=0.9),
        RetrievalHit(chunk_id="2", doc_id="d", text="b", score=0.8),
    ]
    b = [
        RetrievalHit(chunk_id="2", doc_id="d", text="b", score=0.95),
        RetrievalHit(chunk_id="3", doc_id="d", text="c", score=0.7),
    ]
    fused = reciprocal_rank_fusion([a, b], k=60)
    ids = [h.chunk_id for h in fused]
    # chunk 2 在两路都靠前，应排最前或靠前
    assert ids[0] == "2"
    assert set(ids) == {"1", "2", "3"}


def test_bm25_sparse_nonempty():
    from rag.sparse import bm25_sparse

    vec = bm25_sparse("企业知识库检索 Agent 平台")
    assert vec
    assert all(isinstance(k, int) and v > 0 for k, v in vec.items())


def test_pricing_estimate():
    from shared.pricing import estimate_cost_usd

    cost = estimate_cost_usd("gpt-4o-mini", prompt_tokens=1_000_000, completion_tokens=0)
    assert abs(cost - 0.15) < 1e-9
