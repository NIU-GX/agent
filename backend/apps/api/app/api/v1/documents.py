"""文档上传与入库任务查询：MinIO 存原文，Postgres 存状态，RabbitMQ 驱动流水线。"""

from __future__ import annotations

from pathlib import PurePath

from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile

from shared.config import settings
from shared.rag_store import Principal
from shared.schemas import DocumentStatus

from app.core.security import require_principal

router = APIRouter()


_ALLOWED_SUFFIXES = {".md", ".txt", ".json", ".csv", ".log", ".html", ".htm", ".pdf", ".docx"}


async def _validate_upload(file: UploadFile) -> tuple[str, int]:
    filename = PurePath(file.filename or "upload.bin").name
    suffix = PurePath(filename).suffix.lower()
    if suffix not in _ALLOWED_SUFFIXES:
        raise HTTPException(status_code=415, detail="unsupported file type")
    total, head = 0, b""
    while chunk := await file.read(1024 * 1024):
        total += len(chunk)
        if len(head) < 4096:
            head += chunk[: 4096 - len(head)]
        if total > settings.rag_max_upload_bytes:
            raise HTTPException(status_code=413, detail="file exceeds upload limit")
    if total == 0:
        raise HTTPException(status_code=400, detail="empty file")
    if suffix == ".pdf" and not head.startswith(b"%PDF-"):
        raise HTTPException(status_code=415, detail="invalid pdf signature")
    if suffix == ".docx" and not head.startswith(b"PK"):
        raise HTTPException(status_code=415, detail="invalid docx signature")
    await file.seek(0)
    return filename, total


async def _upload_document(
    *, request: Request, file: UploadFile, principal: Principal, kb_id: str
):
    container = request.app.state.container
    try:
        await container.rag_store.ensure_kb_access(principal, kb_id, write=True)
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    filename, size = await _validate_upload(file)
    # 使用临时 UUID 前缀；创建 version 后不会覆盖任何已有原文。
    import uuid

    staging_id = str(uuid.uuid4())
    key = f"raw/{principal.tenant_id}/{kb_id}/{staging_id}/{filename}"
    put_file = getattr(container.object_store, "put_file", None)
    if put_file:
        await put_file(key, file.file, size, file.content_type or "application/octet-stream")
    else:
        await container.object_store.put_bytes(key, await file.read(), file.content_type or "application/octet-stream")
    record = await container.rag_store.create_document_with_outbox(
        tenant_id=principal.tenant_id, kb_id=kb_id, filename=filename, blob_key=key
    )
    return {**record, "status": DocumentStatus.UPLOADED.value}


@router.post("/knowledge-bases/{kb_id}/documents")
async def upload_document(
    request: Request,
    kb_id: str,
    file: UploadFile = File(...),
    principal: Principal = Depends(require_principal),
):
    return await _upload_document(request=request, file=file, principal=principal, kb_id=kb_id)


@router.post("/documents", deprecated=True)
async def upload_legacy_document(
    request: Request, file: UploadFile = File(...), principal: Principal = Depends(require_principal)
):
    """迁移期兼容入口，写入 legacy KB；新客户端必须使用 KB 路径。"""
    if principal.tenant_id != settings.legacy_tenant_id:
        raise HTTPException(status_code=410, detail="use /knowledge-bases/{kb_id}/documents")
    return await _upload_document(
        request=request, file=file, principal=principal, kb_id=settings.legacy_knowledge_base_id
    )


@router.get("/documents")
async def list_documents(request: Request, principal: Principal = Depends(require_principal)):
    kb_ids = {item["id"] for item in await request.app.state.container.rag_store.list_knowledge_bases(principal)}
    items = await request.app.state.container.rag_store.list_documents_for_scope(principal, kb_ids)
    return {"items": items}


@router.get("/jobs/{job_id}")
async def get_job(job_id: str, request: Request, principal: Principal = Depends(require_principal)):
    job = await request.app.state.container.rag_store.get_stage_run(principal, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="job not found")
    return job


@router.post("/jobs/{job_id}/replay", status_code=202)
async def replay_job(job_id: str, request: Request, principal: Principal = Depends(require_principal)):
    try:
        await request.app.state.container.rag_store.replay_failed_stage(principal, job_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    return {"job_id": job_id, "status": "queued"}
