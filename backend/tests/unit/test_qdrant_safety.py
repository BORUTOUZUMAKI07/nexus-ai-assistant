from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from backend.app.infrastructure.vector.qdrant_client import QdrantService


@pytest.mark.asyncio
async def test_dimension_mismatch_never_deletes_existing_collection(monkeypatch):
    service = QdrantService()
    service.client = SimpleNamespace(
        get_collections=AsyncMock(
            return_value=SimpleNamespace(
                collections=[SimpleNamespace(name="nexus_knowledge")]
            )
        ),
        get_collection=AsyncMock(
            return_value=SimpleNamespace(
                config=SimpleNamespace(
                    params=SimpleNamespace(
                        vectors={"dense": SimpleNamespace(size=384)}
                    )
                )
            )
        ),
        delete_collection=AsyncMock(),
        create_collection=AsyncMock(),
    )
    monkeypatch.setattr(
        "backend.app.infrastructure.vector.qdrant_client.COLLECTION_NAME",
        "nexus_knowledge",
    )
    monkeypatch.setattr(
        "backend.app.infrastructure.vector.qdrant_client.settings.EMBEDDING_DIMENSION",
        768,
    )

    with pytest.raises(ValueError, match="explicit reindex"):
        await service.ensure_collection()

    service.client.delete_collection.assert_not_awaited()
    service.client.create_collection.assert_not_awaited()


def test_collection_recreation_is_opt_in():
    import inspect

    parameter = inspect.signature(QdrantService.ensure_collection).parameters[
        "recreate_if_dimension_mismatch"
    ]
    assert parameter.default is False
