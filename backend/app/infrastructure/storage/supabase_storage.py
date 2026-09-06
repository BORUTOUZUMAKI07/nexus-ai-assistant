"""
Supabase Storage Client.
Uses the Supabase REST Storage API to upload, download, and delete files
from Supabase Storage buckets — no extra S3/boto3 dependency needed.
"""

import httpx
import structlog
from backend.app.core.config import settings
from backend.app.infrastructure.storage.base import IStorageService

logger = structlog.get_logger(__name__)

STORAGE_BUCKET = "nexus-knowledge"


class SupabaseStorageClient(IStorageService):
    """Thin async wrapper around Supabase Storage REST API."""

    def __init__(self) -> None:
        self.base_url = f"{settings.SUPABASE_URL}/storage/v1"
        self.headers = {
            "apikey": settings.SUPABASE_SERVICE_ROLE_KEY or "",
            "Authorization": f"Bearer {settings.SUPABASE_SERVICE_ROLE_KEY or ''}",
        }
        self._bucket_ensured = False

    async def _ensure_bucket(self) -> None:
        """Create the storage bucket if it doesn't exist (idempotent)."""
        if self._bucket_ensured:
            return
        async with httpx.AsyncClient(timeout=15) as client:
            # Check if bucket exists
            resp = await client.get(
                f"{self.base_url}/bucket/{STORAGE_BUCKET}",
                headers=self.headers,
            )
            if resp.status_code == 200:
                self._bucket_ensured = True
                return

            # Create bucket (public=False for private files)
            resp = await client.post(
                f"{self.base_url}/bucket",
                headers=self.headers,
                json={
                    "id": STORAGE_BUCKET,
                    "name": STORAGE_BUCKET,
                    "public": False,
                    "file_size_limit": 52428800,  # 50MB
                    "allowed_mime_types": [
                        "application/pdf",
                        "text/plain",
                        "text/markdown",
                        "text/csv",
                        "application/json",
                        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                        "image/png",
                        "image/jpeg",
                        "image/gif",
                        "image/webp",
                    ],
                },
            )
            if resp.status_code in (200, 201):
                logger.info("supabase_storage_bucket_created", bucket=STORAGE_BUCKET)
                self._bucket_ensured = True
            elif "already exists" in resp.text.lower():
                self._bucket_ensured = True
            else:
                logger.error("supabase_storage_bucket_create_failed", status=resp.status_code, body=resp.text)

    async def upload(
        self,
        file_bytes: bytes,
        storage_path: str,
        content_type: str = "application/octet-stream",
    ) -> str:
        """
        Upload a file to Supabase Storage.

        Args:
            file_bytes: Raw file bytes to upload
            storage_path: Path within the bucket (e.g., 'user_id/uuid.pdf')
            content_type: MIME type of the file

        Returns:
            The full storage path (bucket/path) for reference
        """
        await self._ensure_bucket()

        upload_headers = {
            **self.headers,
            "Content-Type": content_type,
            "x-upsert": "true",  # Overwrite if exists
        }

        async with httpx.AsyncClient(timeout=60) as client:
            resp = await client.post(
                f"{self.base_url}/object/{STORAGE_BUCKET}/{storage_path}",
                headers=upload_headers,
                content=file_bytes,
            )

            if resp.status_code in (200, 201):
                logger.info("supabase_storage_upload_ok", path=storage_path, size=len(file_bytes))
                return f"{STORAGE_BUCKET}/{storage_path}"
            else:
                logger.error("supabase_storage_upload_failed", status=resp.status_code, body=resp.text)
                raise RuntimeError(f"Supabase Storage upload failed: {resp.status_code} - {resp.text}")

    async def download(self, storage_path: str) -> bytes:
        """Download a file from Supabase Storage."""
        await self._ensure_bucket()

        async with httpx.AsyncClient(timeout=60) as client:
            resp = await client.get(
                f"{self.base_url}/object/{STORAGE_BUCKET}/{storage_path}",
                headers=self.headers,
            )
            if resp.status_code == 200:
                return resp.content
            else:
                raise RuntimeError(f"Supabase Storage download failed: {resp.status_code}")

    async def get_public_url(self, storage_path: str) -> str:
        """Get a signed URL for temporary access (60 min expiry)."""
        await self._ensure_bucket()

        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(
                f"{self.base_url}/object/sign/{STORAGE_BUCKET}/{storage_path}",
                headers=self.headers,
                json={"expiresIn": 3600},
            )
            if resp.status_code == 200:
                data = resp.json()
                return f"{settings.SUPABASE_URL}/storage/v1{data['signedURL']}"
            else:
                raise RuntimeError(f"Supabase Storage sign URL failed: {resp.status_code}")

    async def delete(self, storage_path: str) -> bool:
        """Delete a file from Supabase Storage."""
        await self._ensure_bucket()

        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.delete(
                f"{self.base_url}/object/{STORAGE_BUCKET}",
                headers=self.headers,
                json={"prefixes": [storage_path]},
            )
            if resp.status_code == 200:
                logger.info("supabase_storage_deleted", path=storage_path)
                return True
            else:
                logger.warning("supabase_storage_delete_failed", status=resp.status_code, body=resp.text)
                return False

    async def list_files(self, prefix: str = "", limit: int = 100) -> list:
        """List files in the bucket with an optional prefix filter."""
        await self._ensure_bucket()

        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(
                f"{self.base_url}/object/list/{STORAGE_BUCKET}",
                headers=self.headers,
                json={"prefix": prefix, "limit": limit, "sortBy": {"column": "created_at", "order": "desc"}},
            )
            if resp.status_code == 200:
                return resp.json()
            return []


# Singleton instance for backwards-compatibility
storage_client = SupabaseStorageClient()


def get_storage_service() -> IStorageService:
    """Dependency provider returning the active IStorageService implementation."""
    return storage_client
