"""The FastEmbed fallback in services/rag/retrieval.py must use the fastembed
model name, never the Gemini endpoint id.

The offline embedding path was broken on two axes before this module existed:
  1. fastembed was not declared as a dependency, so
     ``from fastembed import TextEmbedding`` raised ModuleNotFoundError the
     moment GEMINI_API_KEY was unset (pyproject now declares
     ``qdrant-client[fastembed]``);
  2. the fallback handed EMBEDDING_MODEL ("models/gemini-embedding-001") to
     fastembed, which can never load a non-fastembed model id (the call site
     now uses settings.FASTEMBED_MODEL).

Axis 1 is guaranteed by the dependency manifest; axis 2 is what these tests pin
behaviourally. Reverting the fallback call site back to ``self.embedding_model``
turns the first test red.
"""
import pytest
from backend.app.core.config import settings
from backend.app.services.rag.retrieval import RetrievalService


class _FakeModel:
    pass


@pytest.fixture
def unconfigured_gemini(monkeypatch):
    """Simulate a deployment with no GEMINI_API_KEY (the fallback's trigger)."""
    monkeypatch.setattr(settings, "GEMINI_API_KEY", "", raising=False)
    monkeypatch.setattr(settings, "EMBEDDING_MODEL", "models/gemini-embedding-001", raising=False)
    monkeypatch.setattr(settings, "FASTEMBED_MODEL", "BAAI/bge-base-en-v1.5", raising=False)


async def test_fallback_uses_fastembed_model_name_not_gemini_id(
    monkeypatch, unconfigured_gemini
):
    """No Gemini key => the local path runs, and it asks fastembed for
    FASTEMBED_MODEL, never for the Gemini id."""
    import backend.app.services.rag.retrieval as mod

    captured: dict[str, object] = {}

    def fake_get_model(model_name: str):
        captured["model"] = model_name
        return _FakeModel()

    def fake_embed(model, texts):
        captured["texts"] = texts
        return [[0.1] * settings.EMBEDDING_DIMENSION for _ in texts]

    monkeypatch.setattr(mod, "_get_fastembed_model", fake_get_model)
    monkeypatch.setattr(mod, "_embed_texts_sync", fake_embed)
    # The Gemini branch must not be entered at all.
    async def boom(*args, **kwargs):
        raise AssertionError("Gemini branch must not run without GEMINI_API_KEY")

    monkeypatch.setattr(mod, "_embed_with_gemini", boom)

    svc = RetrievalService()
    vectors = await svc.generate_embeddings_batch(["hello", "world"])

    assert captured["model"] == settings.FASTEMBED_MODEL
    assert captured["model"] != "models/gemini-embedding-001"
    assert captured["texts"] == ["hello", "world"]
    assert len(vectors) == 2
    assert all(len(v) == settings.EMBEDDING_DIMENSION for v in vectors)


async def test_gemini_branch_wins_when_key_set_and_model_is_gemini(monkeypatch):
    """With GEMINI_API_KEY set and a gemini-model id, the API path is used and
    the fastembed getter is never invoked."""
    import backend.app.services.rag.retrieval as mod

    monkeypatch.setattr(settings, "GEMINI_API_KEY", "test-key", raising=False)
    monkeypatch.setattr(settings, "EMBEDDING_MODEL", "models/gemini-embedding-001", raising=False)

    async def fake_gemini(texts, model, dimension, api_key):
        assert texts == ["hello"]
        assert model == "models/gemini-embedding-001"
        assert api_key == "test-key"
        return [[0.5] * dimension]

    monkeypatch.setattr(mod, "_embed_with_gemini", fake_gemini)

    def must_not_run(model_name):
        raise AssertionError("fastembed getter must not run on the Gemini path")

    monkeypatch.setattr(mod, "_get_fastembed_model", must_not_run)

    svc = RetrievalService()
    vectors = await svc.generate_embeddings_batch(["hello"])

    assert len(vectors) == 1
    assert all(len(v) == settings.EMBEDDING_DIMENSION for v in vectors)


async def test_fallback_rejects_dimension_mismatch(monkeypatch, unconfigured_gemini):
    """A fastembed model whose vectors do not match EMBEDDING_DIMENSION must
    fail loudly rather than silently indexing mis-dimensioned vectors."""
    import backend.app.services.rag.retrieval as mod

    monkeypatch.setattr(mod, "_get_fastembed_model", lambda name: _FakeModel())
    monkeypatch.setattr(
        mod,
        "_embed_texts_sync",
        lambda model, texts: [[0.1] * (settings.EMBEDDING_DIMENSION - 2) for _ in texts],
    )

    svc = RetrievalService()
    with pytest.raises(RuntimeError, match="embedding dimension"):
        await svc.generate_embeddings_batch(["hello"])
