"""
Abstract Storage Service Interface.
Enforces Interface Segregation and Dependency Inversion principles for cloud/local object stores.
"""
from abc import ABC, abstractmethod


class IStorageService(ABC):
    """
    Abstract contract for file/object storage providers (Supabase, S3, GCS, Local).
    """

    @abstractmethod
    async def upload(
        self,
        file_bytes: bytes,
        storage_path: str,
        content_type: str = "application/octet-stream",
    ) -> str:
        """Upload raw bytes to storage and return canonical storage path."""
        pass

    @abstractmethod
    async def download(self, storage_path: str) -> bytes:
        """Download raw bytes from storage."""
        pass

    @abstractmethod
    async def delete(self, storage_path: str) -> bool:
        """Delete an object from storage."""
        pass

    @abstractmethod
    async def get_public_url(self, storage_path: str) -> str:
        """Obtain a signed or public URL for temporary download access."""
        pass

    @abstractmethod
    async def list_files(self, prefix: str = "", limit: int = 100) -> list:
        """List files in storage matching an optional prefix."""
        pass
