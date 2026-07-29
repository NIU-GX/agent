"""MinIO 对象存储封装。"""

from __future__ import annotations

import asyncio
from typing import BinaryIO

from shared.config import settings
from shared.logging import get_logger

logger = get_logger(__name__)


class MinioObjectStore:
    """异步友好的同步客户端包装（minio SDK 为同步，放线程池亦可后续优化）。"""

    def __init__(self) -> None:
        from minio import Minio

        self.client = Minio(
            settings.minio_endpoint,
            access_key=settings.minio_access_key,
            secret_key=settings.minio_secret_key,
            secure=settings.minio_secure,
        )
        self.bucket = settings.minio_bucket
        self._ensure_bucket()

    def _ensure_bucket(self) -> None:
        if not self.client.bucket_exists(self.bucket):
            self.client.make_bucket(self.bucket)
            logger.info("created minio bucket=%s", self.bucket)

    async def get_bytes(self, key: str) -> bytes:
        return await asyncio.to_thread(self._get_bytes, key)

    def _get_bytes(self, key: str) -> bytes:
        response = self.client.get_object(self.bucket, key)
        try:
            return response.read()
        finally:
            response.close()
            response.release_conn()

    async def put_bytes(
        self,
        key: str,
        data: bytes,
        content_type: str = "application/octet-stream",
    ) -> None:
        import io

        await self.put_file(key, io.BytesIO(data), len(data), content_type)

    async def put_file(
        self, key: str, data: BinaryIO, length: int, content_type: str = "application/octet-stream"
    ) -> None:
        await asyncio.to_thread(self._put_file, key, data, length, content_type)

    def _put_file(self, key: str, data: BinaryIO, length: int, content_type: str) -> None:
        self.client.put_object(
            self.bucket,
            key,
            data,
            length=length,
            content_type=content_type,
        )
