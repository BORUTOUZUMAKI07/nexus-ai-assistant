from unittest.mock import AsyncMock

import pytest

from backend.app.infrastructure.vector.qdrant_client import QdrantService


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "filters",
    [{}, {"tenant_id": "x"}, {"file_id": ""}, {"file_id": None}],
)
async def test_delete_by_filter_rejects_unsafe_filters(filters):
    service = QdrantService()
    service.client = AsyncMock()

    with pytest.raises(ValueError):
        await service.delete_by_filter(filters)

    service.client.delete.assert_not_awaited()


@pytest.mark.asyncio
async def test_delete_by_filter_accepts_file_id_filter():
    service = QdrantService()
    service.client = AsyncMock()

    await service.delete_by_filter({"file_id": "file-123"})

    service.client.delete.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("limit", [0, -1, 101, True, 1.5])
async def test_hybrid_search_rejects_invalid_limit(limit):
    service = QdrantService()
    service.client = AsyncMock()

    with pytest.raises(ValueError):
        await service.hybrid_search_with_fusion(
            dense_vector=[0.0] * 768,
            sparse_indices=[],
            sparse_values=[],
            limit=limit,
        )

    service.client.query_points.assert_not_awaited()


@pytest.mark.asyncio
async def test_hybrid_search_rejects_mismatched_sparse_vectors():
    service = QdrantService()
    service.client = AsyncMock()

    with pytest.raises(ValueError):
        await service.hybrid_search_with_fusion(
            dense_vector=[0.0] * 768,
            sparse_indices=[1, 2],
            sparse_values=[0.5],
        )

    service.client.query_points.assert_not_awaited()
