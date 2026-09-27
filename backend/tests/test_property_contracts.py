"""
Hypothesis property tests over pure, deterministic contracts.

Unlike example-driven unit tests, these lock the *invariants* of three pieces
of the system that must hold for every input:

  * ``rate_limit._build_header_payload`` — the RFC 6585 header contract (all
    header values are strings, remaining is clamped at 0, limit/window are
    losslessly round-trippable).
  * ``RetrievalService.generate_sparse_vector`` — the sparse BM25 hashing
    contract: stable across calls (the md5-based index is the whole point when
    the indexing worker and the query path run in separate processes), every
    index lands in [0, 100_000), indices never repeat, values are positive,
    and the bucket hashes conserve token counts even when two words collide.
  * Webhook envelope serialization — `json.dumps(body, default=str)` is the
    wire format the signed-delivery contract builds on; it must round-trip and
    keep the exact envelope keys for any payload shape.

Fail-open by design: every property is derivable from pure local state — no
database, no network, no model weights.
"""
import json
import re
from collections import Counter
from datetime import UTC, datetime

from hypothesis import given, settings
from hypothesis import strategies as st

from backend.app.infrastructure.resilience.rate_limit import _build_header_payload
from backend.app.services.rag.retrieval import RetrievalService

# generate_sparse_vector touches no instance state (it only needs the class to
# exist), so we skip __init__ entirely and avoid constructing the vector store.
_SPARSE_RETRIEVAL = object.__new__(RetrievalService)


# ─────────────────────────────────────────────────────────────────────
# RFC 6585 rate-limit header contract
# ─────────────────────────────────────────────────────────────────────

@given(limit=st.integers(min_value=1, max_value=10_000), remaining=st.integers(min_value=-100, max_value=10_000), window_seconds=st.integers(min_value=1, max_value=3600))
@settings(max_examples=200, deadline=None)
def test_rate_limit_headers_always_strings_and_clamped(limit, remaining, window_seconds):
    headers = _build_header_payload(limit, remaining, window_seconds)

    assert len(headers) == 3
    for value in headers.values():
        assert isinstance(value, str)
        assert value.isdigit()

    # remaining is clamped at 0 — a negative counter must never leak as a
    # "negative requests left" header.
    assert int(headers["X-RateLimit-Remaining"]) == max(0, remaining)
    assert int(headers["X-RateLimit-Limit"]) == limit
    assert int(headers["X-RateLimit-Reset"]) == window_seconds


# ─────────────────────────────────────────────────────────────────────
# Sparse BM25 hashing contract
# ─────────────────────────────────────────────────────────────────────

@settings(max_examples=200, deadline=None)
@given(blank=st.text(max_size=1))
def test_sparse_vector_empty_text_is_empty(blank):
    vector = _SPARSE_RETRIEVAL.generate_sparse_vector(blank)
    assert set(vector) == {"indices", "values"}
    # Regex tokenization can ignore whitespace-only input.
    assert len(vector["indices"]) == len(vector["values"])
    if not re.search(r"\w", blank):
        assert vector["indices"] == []
        assert vector["values"] == []


@settings(max_examples=100, deadline=None)
@given(text=st.text(min_size=1, max_size=200))
def test_sparse_vector_stable_unique_and_bounded(text):
    first = _SPARSE_RETRIEVAL.generate_sparse_vector(text)
    second = _SPARSE_RETRIEVAL.generate_sparse_vector(text)

    # Stability: the async indexing worker and the querying process must derive
    # identical indices (builtin hash() would be randomized per process).
    assert first == second
    assert first["indices"] == second["indices"]

    indices, values = first["indices"], first["values"]
    assert len(indices) == len(values)
    # Unique indices — Qdrant 422s on duplicate sparse indices.
    assert len(set(indices)) == len(indices)
    # Bucket bound agreed between indexer and queryer (mod 100_000).
    assert all(0 <= i < 100_000 for i in indices)
    assert all(v > 0 for v in values)

    # Token-count conservation: hashing collisions merge counts by summing, so
    # the bucketed values always add back up to the raw token total.
    token_total = sum(Counter(re.findall(r"\w+", text.lower())).values())
    # Minimal float drift (float(c) of small ints is exact) — allow 1e-9 slack.
    assert abs(sum(values) - token_total) < 1e-9


# ─────────────────────────────────────────────────────────────────────
# Webhook envelope wire-format contract
# ─────────────────────────────────────────────────────────────────────

WebhookEnvelope = st.fixed_dictionaries(
    {
        "event": st.text(min_size=1),
        "delivery_id": st.uuids().map(str),
        "timestamp": st.datetimes(timezones=st.just(UTC)).map(lambda dt: dt.isoformat()),
        "payload": st.one_of(
            st.none(),
            st.booleans(),
            st.integers(),
            st.floats(allow_nan=False),
            st.text(),
            st.lists(st.integers()),
            st.dictionaries(st.text(), st.integers(), max_size=5),
        ),
    }
)


@given(envelope=WebhookEnvelope)
@settings(max_examples=100, deadline=None)
def test_webhook_envelope_raw_body_round_trips(envelope):
    # The exact serializer used by ``WebhookService._deliver`` for signing.
    raw = json.dumps(envelope, default=str).encode("utf-8")

    decoded = json.loads(raw.decode("utf-8"))
    assert set(decoded.keys()) == {"event", "delivery_id", "timestamp", "payload"}
    assert decoded["event"] == envelope["event"]
    assert decoded["delivery_id"] == envelope["delivery_id"]
    assert decoded["payload"] == envelope["payload"]

    # The timestamp is machine-readable ISO-8601 with a UTC offset — consumers
    # must be able to parse it for replay-window checks.
    parsed = datetime.fromisoformat(decoded["timestamp"])
    assert parsed.tzinfo is not None