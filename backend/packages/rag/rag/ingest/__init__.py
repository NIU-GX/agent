"""入库各阶段实现：parse / chunk / embed / index。

每个 stage 函数签名统一为 async (msg, deps) -> None，便于 Worker 注册与单测。
"""

from __future__ import annotations

import hashlib
import json
import re
import uuid
from zipfile import ZipFile
from pathlib import Path
from typing import Any, Protocol

from shared.logging import get_logger
from shared.config import settings

from rag.models import Chunk, ParsedDocument, QueueMessage

logger = get_logger(__name__)


class ObjectStore(Protocol):
    """MinIO 抽象，便于单测 mock。"""

    async def get_bytes(self, key: str) -> bytes: ...

    async def put_bytes(self, key: str, data: bytes, content_type: str = "application/octet-stream") -> None: ...


class VectorStore(Protocol):
    """Milvus 抽象。"""

    async def upsert_chunks(self, chunks: list[Chunk]) -> None: ...

    async def delete_by_doc_id(self, doc_id: str) -> None: ...


class Embedder(Protocol):
    async def embed(self, texts: list[str]) -> list[list[float]]: ...


class StatusUpdater(Protocol):
    """更新 Postgres 中文档/Job 状态。"""

    async def set_document_status(self, doc_id: str, status: str, *, error: str | None = None, chunk_count: int | None = None) -> None: ...

    async def set_job_stage(self, job_id: str, stage: str, status: str, *, error: str | None = None, attempt: int = 0) -> None: ...


# ---------------------------------------------------------------------------
# Parse
# ---------------------------------------------------------------------------

async def run_parse(
    msg: QueueMessage,
    *,
    store: ObjectStore,
    status: StatusUpdater,
) -> None:
    """从 MinIO 拉取原文，解析为纯文本，写回中间产物。"""
    await status.set_job_stage(msg.job_id, "parse", "running", attempt=msg.attempt)
    await status.set_document_status(msg.doc_id, "parsing")

    raw = await store.get_bytes(msg.payload_ref)
    filename = Path(msg.payload_ref).name
    text = _extract_text(raw, filename)

    parsed = ParsedDocument(
        doc_id=msg.doc_id,
        filename=filename,
        text=text,
        metadata={
            "source_key": msg.payload_ref,
            "tenant_id": msg.tenant_id or "",
            "kb_id": msg.kb_id or "",
            "document_version_id": msg.document_version_id or "",
        },
    )
    out_key = f"parsed/{msg.document_version_id or msg.doc_id}.json"
    await store.put_bytes(out_key, parsed.model_dump_json().encode("utf-8"), "application/json")
    # 后续阶段通过 payload_ref 读取 parsed json
    msg.payload_ref = out_key
    msg.content_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()

    await status.set_job_stage(msg.job_id, "parse", "succeeded", attempt=msg.attempt)
    logger.info("parse done doc_id=%s chars=%s", msg.doc_id, len(text))


def _extract_text(raw: bytes, filename: str) -> str:
    """按扩展名解析文本；docx/html 走专用路径，未知类型严格失败而非静默乱码。"""
    lower = filename.lower()
    if lower.endswith((".md", ".txt", ".json", ".csv", ".log")):
        return raw.decode("utf-8", errors="ignore")
    if lower.endswith((".html", ".htm")):
        text = raw.decode("utf-8", errors="ignore")
        text = re.sub(r"(?is)<script.*?>.*?</script>", " ", text)
        text = re.sub(r"(?is)<style.*?>.*?</style>", " ", text)
        text = re.sub(r"(?s)<[^>]+>", " ", text)
        return re.sub(r"\s+", " ", text).strip()
    if lower.endswith(".pdf"):
        from pypdf import PdfReader
        import io

        reader = PdfReader(io.BytesIO(raw))
        if len(reader.pages) > settings.rag_max_pdf_pages:
            raise ValueError(f"pdf page count exceeds {settings.rag_max_pdf_pages}")
        text = "\n".join(page.extract_text() or "" for page in reader.pages)
        if not text.strip():
            raise ValueError("pdf has no extractable text; OCR is required")
        return text
    if lower.endswith(".docx"):
        try:
            import io
            with ZipFile(io.BytesIO(raw)) as zf:
                total_uncompressed = sum(info.file_size for info in zf.infolist())
                compressed = max(sum(info.compress_size for info in zf.infolist()), 1)
                if total_uncompressed > settings.rag_max_docx_uncompressed_bytes:
                    raise ValueError("docx uncompressed content exceeds limit")
                if total_uncompressed / compressed > settings.rag_max_docx_compression_ratio:
                    raise ValueError("docx compression ratio exceeds limit")
                xml = zf.read("word/document.xml").decode("utf-8", errors="ignore")
            return re.sub(r"<[^>]+>", " ", xml)
        except Exception as exc:  # noqa: BLE001
            raise ValueError(f"docx parse failed: {exc}") from exc
    raise ValueError(f"unsupported file type: {filename}")


# ---------------------------------------------------------------------------
# Chunk
# ---------------------------------------------------------------------------

async def run_chunk(
    msg: QueueMessage,
    *,
    store: ObjectStore,
    status: StatusUpdater,
    chunk_size: int = 800,
    chunk_overlap: int = 120,
) -> None:
    """递归字符切分 + parent-child：父块保存上下文，子块用于检索。"""
    await status.set_job_stage(msg.job_id, "chunk", "running", attempt=msg.attempt)
    await status.set_document_status(msg.doc_id, "chunking")

    parsed = ParsedDocument.model_validate_json(await store.get_bytes(msg.payload_ref))
    chunks = parent_child_chunk(
        parsed,
        child_size=chunk_size,
        child_overlap=chunk_overlap,
        parent_size=chunk_size * 3,
    )
    out_key = f"chunks/{msg.document_version_id or msg.doc_id}.json"
    payload = json.dumps([c.model_dump() for c in chunks], ensure_ascii=False).encode("utf-8")
    await store.put_bytes(out_key, payload, "application/json")
    msg.payload_ref = out_key

    await status.set_document_status(msg.doc_id, "chunking", chunk_count=len(chunks))
    await status.set_job_stage(msg.job_id, "chunk", "succeeded", attempt=msg.attempt)
    logger.info("chunk done doc_id=%s count=%s", msg.doc_id, len(chunks))


def recursive_split(text: str, size: int, overlap: int) -> list[str]:
    """按段落优先的递归切分（简化版 LangChain RecursiveCharacterTextSplitter）。"""
    if len(text) <= size:
        return [text] if text.strip() else []

    separators = ["\n\n", "\n", "。", ".", " ", ""]
    for sep in separators:
        if sep == "":
            parts = [text[i : i + size] for i in range(0, len(text), max(size - overlap, 1))]
            return [p for p in parts if p.strip()]
        if sep in text:
            pieces = text.split(sep)
            break
    else:
        pieces = [text]

    chunks: list[str] = []
    buf = ""
    for piece in pieces:
        candidate = f"{buf}{sep}{piece}" if buf else piece
        if len(candidate) <= size:
            buf = candidate
        else:
            if buf.strip():
                chunks.append(buf.strip())
            if len(piece) > size:
                chunks.extend(recursive_split(piece, size, overlap))
                buf = ""
            else:
                buf = piece
    if buf.strip():
        chunks.append(buf.strip())
    if overlap <= 0 or len(chunks) < 2:
        return chunks
    # 分隔符切分也要保留重叠，避免句子/段落边界信息丢失。
    return [chunks[0]] + [chunks[i - 1][-overlap:] + "\n" + chunk for i, chunk in enumerate(chunks[1:], 1)]


def parent_child_chunk(
    parsed: ParsedDocument,
    *,
    child_size: int,
    child_overlap: int,
    parent_size: int,
) -> list[Chunk]:
    """Parent-Child：父块承载宽上下文，子块做向量检索命中后回填父块。"""
    parents = recursive_split(parsed.text, parent_size, overlap=parent_size // 10)
    results: list[Chunk] = []
    for parent_text in parents:
        parent_id = str(uuid.uuid4())
        # 父块本身也入库，便于直接展示宽上下文
        results.append(
            Chunk(
                chunk_id=parent_id,
                doc_id=parsed.doc_id,
                parent_id=None,
                text=parent_text,
                metadata={
                    **parsed.metadata,
                    "filename": parsed.filename,
                    "kind": "parent",
                    "source": parsed.filename,
                },
            )
        )
        for child_text in recursive_split(parent_text, child_size, child_overlap):
            results.append(
                Chunk(
                    chunk_id=str(uuid.uuid4()),
                    doc_id=parsed.doc_id,
                    parent_id=parent_id,
                    text=child_text,
                    metadata={
                        **parsed.metadata,
                        "filename": parsed.filename,
                        "kind": "child",
                        "source": parsed.filename,
                    },
                )
            )
    return results


# ---------------------------------------------------------------------------
# Embed
# ---------------------------------------------------------------------------

async def run_embed(
    msg: QueueMessage,
    *,
    store: ObjectStore,
    embedder: Embedder,
    status: StatusUpdater,
    batch_size: int = 32,
) -> None:
    """批量调用网关 embedding，写回带向量的 chunks。"""
    await status.set_job_stage(msg.job_id, "embed", "running", attempt=msg.attempt)
    await status.set_document_status(msg.doc_id, "embedding")

    raw = json.loads((await store.get_bytes(msg.payload_ref)).decode("utf-8"))
    chunks = [Chunk.model_validate(item) for item in raw]
    children = [c for c in chunks if c.metadata.get("kind") == "child"]
    texts = [c.text for c in children]

    vectors: list[list[float]] = []
    for i in range(0, len(texts), batch_size):
        batch = texts[i : i + batch_size]
        vectors.extend(await embedder.embed(batch))

    if len(children) != len(vectors):
        raise ValueError(f"embed size mismatch: chunks={len(children)} vectors={len(vectors)}")
    from rag.sparse import bm25_sparse

    for chunk, vec in zip(children, vectors):
        chunk.dense_vector = vec
        chunk.sparse_vector = bm25_sparse(chunk.text)

    out_key = f"embedded/{msg.document_version_id or msg.doc_id}.json"
    payload = json.dumps([c.model_dump() for c in chunks], ensure_ascii=False).encode("utf-8")
    await store.put_bytes(out_key, payload, "application/json")
    msg.payload_ref = out_key

    await status.set_job_stage(msg.job_id, "embed", "succeeded", attempt=msg.attempt)
    logger.info("embed done doc_id=%s count=%s", msg.doc_id, len(chunks))


# ---------------------------------------------------------------------------
# Index
# ---------------------------------------------------------------------------

async def run_index(
    msg: QueueMessage,
    *,
    store: ObjectStore,
    vector_store: VectorStore,
    status: StatusUpdater,
) -> None:
    """幂等写入 Milvus：先按 doc_id 删除旧版本，再 upsert。"""
    await status.set_job_stage(msg.job_id, "index", "running", attempt=msg.attempt)
    await status.set_document_status(msg.doc_id, "indexing")

    raw = json.loads((await store.get_bytes(msg.payload_ref)).decode("utf-8"))
    chunks = [Chunk.model_validate(item) for item in raw]

    # 不删除旧版本：新 version 完整写入后由元数据层原子切换 active version。
    children = [c for c in chunks if c.metadata.get("kind") == "child"]
    await vector_store.upsert_chunks(children)

    await status.set_document_status(msg.doc_id, "ready", chunk_count=len(children))
    await status.set_job_stage(msg.job_id, "index", "succeeded", attempt=msg.attempt)
    logger.info("index done doc_id=%s count=%s", msg.doc_id, len(children))
