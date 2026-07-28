"""入库流水线编排辅助：阶段顺序、状态映射、幂等键。"""

from __future__ import annotations

from shared.schemas import DocumentStatus, RagStage

STAGE_ORDER: list[RagStage] = [
    RagStage.PARSE,
    RagStage.CHUNK,
    RagStage.EMBED,
    RagStage.INDEX,
]

STAGE_TO_DOC_STATUS: dict[RagStage, DocumentStatus] = {
    RagStage.PARSE: DocumentStatus.PARSING,
    RagStage.CHUNK: DocumentStatus.CHUNKING,
    RagStage.EMBED: DocumentStatus.EMBEDDING,
    RagStage.INDEX: DocumentStatus.INDEXING,
}


def next_stage(stage: RagStage) -> RagStage | None:
    try:
        idx = STAGE_ORDER.index(stage)
    except ValueError:
        return None
    if idx + 1 >= len(STAGE_ORDER):
        return None
    return STAGE_ORDER[idx + 1]


def idempotency_key(doc_id: str, stage: str, content_hash: str) -> str:
    return f"{doc_id}:{stage}:{content_hash}"
