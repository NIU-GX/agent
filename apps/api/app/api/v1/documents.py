"""文档上传与入库任务查询：MinIO 存原文，Postgres 存状态，RabbitMQ 驱动流水线。"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile

from rag.models import QueueMessage
from shared.schemas import DocumentStatus, RagStage

from app.core.security import require_api_key

router = APIRouter()


@router.post("/documents")
async def upload_document(
    request: Request,
    file: UploadFile = File(...),
    _: None = Depends(require_api_key),
):
    container = request.app.state.container
    doc_id = str(uuid.uuid4())
    job_id = str(uuid.uuid4())
    filename = file.filename or "upload.bin"
    data = await file.read()
    if not data:
        raise HTTPException(status_code=400, detail="empty file")

    key = f"raw/{doc_id}/{filename}"
    await container.object_store.put_bytes(key, data, file.content_type or "application/octet-stream")

    await container.status.create_document(
        doc_id=doc_id,
        filename=filename,
        blob_key=key,
        status=DocumentStatus.UPLOADED.value,
    )
    await container.status.create_job(
        job_id=job_id,
        doc_id=doc_id,
        stage=RagStage.PARSE.value,
        status="queued",
    )

    if not container.publisher:
        raise HTTPException(status_code=503, detail="RabbitMQ not available")

    await container.publisher.publish(
        QueueMessage(
            job_id=job_id,
            doc_id=doc_id,
            stage=RagStage.PARSE,
            payload_ref=key,
            attempt=0,
        )
    )
    return {"doc_id": doc_id, "job_id": job_id, "status": DocumentStatus.UPLOADED.value}


@router.get("/documents")
async def list_documents(request: Request, _: None = Depends(require_api_key)):
    items = await request.app.state.container.status.list_documents()
    return {"items": items}


@router.get("/jobs/{job_id}")
async def get_job(job_id: str, request: Request, _: None = Depends(require_api_key)):
    job = await request.app.state.container.status.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="job not found")
    return job
