"""
Tests for D3 - artifact generation.

The premise of the feature: a chat turn that is really a document should end up
as a versioned artifact without the user having to go and ask for it out of
band. Three properties have to hold, and each is asserted here directly rather
than through a mock that would pass regardless:

1. It fires on documents and stays quiet on chat.
2. It costs nothing on the cases that can be decided without a model. A
   tie-breaker LLM call on every turn would be a worse product than no feature.
3. It never costs the user their reply. Every failure path returns, it does not
   raise.
"""

import pytest
from backend.app.services.artifact_intent import (
    IntentConfig,
    classify_media,
    decide,
    derive_title,
)

# ─── fixtures ───────────────────────────────────────────────────────────────

LONG_PROSE = "The system is straightforward. " * 60  # > 1200 chars, no structure

DOCUMENT = (
    "# Incident Review: Checkout Latency\n\n"
    "## Summary\n\n"
    "Latency rose 4x after the 14:02 deploy and returned to baseline at 15:10.\n\n"
    "## Timeline\n\n"
    "14:02 deploy of `checkout-v3`. 14:05 p99 crosses 2s. 14:20 rollback.\n\n"
    "## Impact\n\n"
    "Roughly 18% of checkout attempts were abandoned during the window.\n"
) + ("Supporting detail paragraph. " * 40)

CODE_ANSWER = (
    "Here is the implementation you asked for.\n\n"
    "```python\n"
    "def dedupe(items):\n"
    "    seen = set()\n"
    "    out = []\n"
    "    for item in items:\n"
    "        if item not in seen:\n"
    "            seen.add(item)\n"
    "            out.append(item)\n"
    "    return out\n"
    "```\n\n"
    "It preserves first-seen order, which is usually what you want.\n"
) * 3


class _RecordingClassifier:
    """Stands in for the structured-output service, counting calls."""

    def __init__(self, verdict: bool = True):
        self.verdict = verdict
        self.calls: list[dict] = []

    async def generate_structured(self, **kwargs):
        self.calls.append(kwargs)

        class _V:
            is_durable_artifact = self.verdict

        return _V()


@pytest.fixture
def classifier(monkeypatch):
    rec = _RecordingClassifier()
    import backend.app.services.structured_output as so

    monkeypatch.setattr(so, "structured_service", rec)
    return rec


# ─── layer 1: explicit request, free ─────────────────────────────────────────


@pytest.mark.parametrize(
    "user_text",
    [
        "write me a report on the Q3 numbers",
        "Create a design doc for the new scheduler",
        "draft a spec for the API",
        "Generate a tutorial on RAG",
        "produce a changelog for this release",
        "write a README",
        "make a roadmap for the migration",
        "/doc the incident review",
        "/artifact something",
    ],
)
async def test_an_explicit_request_always_creates(user_text, classifier):
    intent = await decide(
        user_text=user_text, answer="Sure, here it is.", mode="normal"
    )
    assert intent.create, f"missed an explicit request: {user_text!r}"
    assert not intent.used_model, "an explicit request must not need a model call"


@pytest.mark.parametrize(
    "user_text",
    [
        # These mention a document but ask a question *about* one. Firing here
        # is the false positive that trains a user to delete artifacts.
        "explain the architecture document",
        "what does the spec say about retries",
        "summarize the changelog",
        "how long is the report supposed to be",
        "is the readme up to date",
    ],
)
async def test_a_question_about_a_document_does_not_create(user_text):
    """Isolates layer 1.

    The classifier is switched off on purpose. With it on, a substantial answer
    legitimately *can* produce an artifact -- that is what the tie-breaker is
    for -- so leaving it in would test nothing about the request patterns.
    """
    intent = await decide(
        user_text=user_text,
        answer=LONG_PROSE,
        mode="normal",
        config=IntentConfig(allow_classifier=False),
    )
    assert not intent.create, f"a question was read as a request: {user_text!r}"
    assert intent.reason == "no_signal"


async def test_code_mode_always_creates_without_a_model_call(classifier):
    """Code mode exists to produce a runnable thing.

    A code answer left only as chat text is the one case where a file is
    unambiguously right, so asking a model would be paying to be told what the
    mode already said.
    """
    intent = await decide(
        user_text="can you fix this",
        answer="def f():\n    return 1\n",
        mode="code",
    )
    assert intent.create
    assert intent.reason == "code_mode"
    assert not intent.used_model
    assert not classifier.calls


# ─── layer 2: structural signal, free ───────────────────────────────────────


async def test_a_document_shaped_answer_creates(classifier):
    intent = await decide(user_text="how did it go", answer=DOCUMENT, mode="normal")
    assert intent.create
    assert intent.reason == "document_headings"
    assert not intent.used_model
    assert not classifier.calls


async def test_a_long_unstructured_ramble_does_not_create(classifier):
    """Length alone must not be enough.

    A long conversational reply is still a chat reply, and turning every long
    answer into a file is the failure mode that would make this feature
    intolerable within a day. The classifier is off so this measures the free
    signal rather than the fake's verdict.
    """
    intent = await decide(
        user_text="explain things",
        answer=LONG_PROSE,
        mode="normal",
        config=IntentConfig(allow_classifier=False),
    )
    assert not intent.create
    assert intent.reason == "no_signal"
    assert not classifier.calls


async def test_a_short_headed_answer_does_not_create(classifier):
    """Headings, but below the length floor."""
    short = "## One\n\ntext\n\n## Two\n\ntext"
    intent = await decide(
        user_text="hi", answer=short, mode="normal", config=IntentConfig(allow_classifier=False)
    )
    assert not intent.create


async def test_a_code_block_does_not_need_prose_length(classifier):
    """The two structural floors are separate, on purpose.

    A forty-line function is exactly what a user wants to keep, and it is well
    short of a prose document. Holding code to the prose floor loses the
    clearest case in the whole feature.
    """
    short_code = "```python\n" + "    result = transform(item)  # apply\n" * 20 + "```\n"
    assert 400 <= len(short_code) < 1200, (
        f"fixture must sit between the two floors, got {len(short_code)}"
    )
    intent = await decide(
        user_text="write a function", answer=short_code, mode="normal", config=IntentConfig(allow_classifier=False)
    )
    assert intent.create
    assert intent.reason == "code_block"


async def test_a_long_code_snippet_inside_a_long_explanation_does_not_create(classifier):
    """The proportion gate, tested where it is actually load-bearing.

    The single-fence test above cannot prove this rule, because there the code
    is too *short* to be substantial and the share is never consulted. A revert
    harness confirmed that: deleting the `is_dominant` half of the condition
    changed no test's result.

    This fixture is the other side of the same coin -- plenty of code, so
    `is_substantial` is comfortably true -- buried in enough prose that it is a
    tiny fraction of the answer. That is a chat answer that happens to include a
    real code sample, and it must not become a file.
    """
    snippet = "```python\n" + "    result = transform(item)  # apply\n" * 15 + "```\n"
    explanation = (
        "Before reaching the code, it is worth understanding what the scheduler "
        "is actually doing and why the naive approach fails under load. "
    ) * 120
    answer = explanation + snippet + explanation

    assert len(snippet) >= 400, "code must clear the substantial floor"
    share = len(snippet) / len(answer)
    assert share < 0.25, f"fixture must stay under the share floor, got {share:.3f}"

    intent = await decide(
        user_text="how does the scheduler work",
        answer=answer,
        mode="normal",
        config=IntentConfig(allow_classifier=False),
    )
    assert not intent.create, "a code sample inside an explanation is not a file"
    assert intent.reason == "no_signal"


async def test_the_share_floor_is_configurable(classifier):
    """Raising the floor must be able to exclude what the default accepts."""
    code = "```python\n" + "x = compute(y)  # work\n" * 30 + "```\n"
    answer = "Here is the function.\n\n" + code

    relaxed = await decide(
        user_text="q",
        answer=answer,
        mode="normal",
        config=IntentConfig(allow_classifier=False),
    )
    assert relaxed.create

    # The default (0.25) accepts because the code dominates. Requiring 0.99
    # rejects it, because prose framing always puts it just under 1.0.
    strict = await decide(
        user_text="q",
        answer=answer,
        mode="normal",
        config=IntentConfig(allow_classifier=False, min_code_share=0.99),
    )
    assert not strict.create


async def test_a_real_code_block_creates(classifier):
    intent = await decide(user_text="how do I dedupe", answer=CODE_ANSWER, mode="normal")
    assert intent.create
    assert intent.reason == "code_block"
    assert not intent.used_model


async def test_a_single_fence_is_not_a_code_artifact(classifier):
    """One fence inside a long prose answer is quoting code, not producing a file.

    A closed pair is required, so a lone fence -- which is what "here is the one
    line you asked about" actually looks like -- does not turn a chat answer
    into a source artifact.
    """
    prose = "Use the modulo operator to test for evenness. " * 30
    answer = prose + "\n\n```\nx % 2 == 0\n```\n" + prose
    assert len(answer) >= 1200, "fixture must clear the document floor"
    assert len(_fences(answer)) == 2
    intent = await decide(
        user_text="even numbers?", answer=answer, mode="normal", config=IntentConfig(allow_classifier=False)
    )
    assert not intent.create


def _fences(text: str) -> list[str]:
    from backend.app.services.artifact_intent import _FENCE

    return _FENCE.findall(text)


# ─── layer 3: the classifier, and when it may run ────────────────────────────


async def test_the_classifier_breaks_a_tie(classifier):
    """A substantial but unstructured answer is the ambiguous case."""
    answer = "You should probably use a queue here. " * 30
    intent = await decide(user_text="should I use a queue", answer=answer, mode="normal")
    assert intent.create
    assert intent.reason == "classifier"
    assert intent.used_model
    assert len(classifier.calls) == 1


async def test_a_negative_classifier_verdict_means_no_artifact(classifier):
    answer = "You should probably use a queue here. " * 30
    intent = await decide(user_text="should I use a queue", answer=answer, mode="normal")
    assert intent.create  # sanity: the fake says True

    classifier.verdict = False
    intent = await decide(user_text="should I use a queue", answer=answer, mode="normal")
    assert not intent.create
    assert not intent.used_model


async def test_the_classifier_never_runs_when_budget_is_gone(classifier):
    """A turn that has hit its ceiling does not get a new optional call."""
    intent = await decide(
        user_text="should I use a queue",
        answer="You should probably use a queue here. " * 30,
        mode="normal",
        can_spend=lambda: False,
    )
    assert not intent.create
    assert not classifier.calls, "spent an LLM call on an exhausted budget"


async def test_the_classifier_never_runs_on_a_short_answer(classifier):
    intent = await decide(
        user_text="hi", answer="Hello! How can I help you today?", mode="normal"
    )
    assert not intent.create
    assert not classifier.calls


async def test_a_classifier_that_cannot_be_reached_decides_nothing(monkeypatch):
    """A tie-breaker that cannot vote must not become a silent veto-by-error.

    It also must not raise: this layer is optional by construction, and the two
    free layers have already had their say.
    """
    import backend.app.services.structured_output as so

    class _Broken:
        async def generate_structured(self, **kwargs):
            raise RuntimeError("provider is down")

    monkeypatch.setattr(so, "structured_service", _Broken())
    intent = await decide(
        user_text="should I use a queue",
        answer="You should probably use a queue here. " * 30,
        mode="normal",
    )
    assert not intent.create
    assert not intent.used_model


async def test_the_classifier_schema_cannot_express_anything_but_a_yes():
    """The narrow-schema property borrowed from open-canvas.

    The response model has one boolean field, so a hallucinating model cannot
    talk the pipeline into emitting a title, a language, or a rewrite. If a
    second field is ever added here, the guarantee is gone and this test should
    fail rather than be quietly updated.
    """
    from backend.app.services.artifact_intent import _ArtifactVerdict

    assert set(_ArtifactVerdict.model_fields) == {"is_durable_artifact"}


async def test_disabling_the_classifier_leaves_the_free_signals_deciding(classifier):
    cfg = IntentConfig(allow_classifier=False)
    answer = "You should probably use a queue here. " * 30
    intent = await decide(
        user_text="should I use a queue", answer=answer, mode="normal", config=cfg
    )
    assert not intent.create
    assert not classifier.calls

    # ...and the free signals still work with it off.
    intent = await decide(user_text="write a report", answer="x", mode="normal", config=cfg)
    assert intent.create


async def test_the_kill_switch_stops_everything():
    intent = await decide(
        user_text="write me a report",
        answer=DOCUMENT,
        mode="normal",
        config=IntentConfig(enabled=False),
    )
    assert not intent.create
    assert intent.reason == "disabled"


async def test_an_empty_answer_never_creates():
    intent = await decide(user_text="write a report", answer="   ", mode="normal")
    assert not intent.create
    assert intent.reason == "empty_answer"


# ─── metadata derivation ────────────────────────────────────────────────────


def test_the_title_is_the_heading_not_its_first_character():
    """Regression: the heading regex captured only `\\S`.

    Every document artifact was being titled with a single letter, which is
    invisible in a chat reply and useless as an artifact's identity.
    """
    assert derive_title(DOCUMENT, "how did it go") == "Incident Review: Checkout Latency"


def test_the_title_is_deterministic():
    """The versioning contract.

    Regenerating the same document must land on the same title, or every
    regeneration is a duplicate and `artifact_versions` stays empty forever.
    """
    a = derive_title(DOCUMENT, "how did it go")
    b = derive_title(DOCUMENT, "how did it go")
    assert a == b


def test_the_title_falls_back_to_prose_when_there_are_no_headings():
    answer = "A queue is the right primitive here. It decouples producers."
    assert derive_title(answer, "should I use a queue") == answer


def test_the_title_skips_fences_and_tables():
    answer = "```python\nx = 1\n```\n\n| a | b |\n|---|---|\n\nReal opening line."
    assert derive_title(answer, "q") == "Real opening line."


def test_the_title_falls_back_to_the_request():
    assert derive_title("", "  what   is  an   artifact  ") == "what is an artifact"


def test_an_answerless_request_still_gets_a_title():
    assert derive_title("", "") == "Untitled artifact"


def test_a_very_long_heading_is_capped():
    assert len(derive_title("# " + "x" * 500, "q")) == 200


def test_a_declared_fence_language_becomes_the_artifact_language():
    assert classify_media("```python\nx=1\n```\n") == ("python", "text/x-source")
    assert classify_media("```TypeScript\nx\n```\n") == ("typescript", "text/x-source")


def test_an_undeclared_fence_is_markdown_not_code():
    """A bare fence quoting code in prose must not claim to be a source file.

    Guessing a language would open the file in the editor with the wrong
    syntax highlighting, which is a worse outcome than calling it markdown.
    """
    assert classify_media("```\nx % 2\n```\n") == ("markdown", "text/markdown")


def test_a_bogus_language_token_is_not_taken_as_a_language():
    """Otherwise a fence followed by prose becomes a nonsense file type."""
    assert classify_media("```Here's the plan\nx\n```\n") == (
        "markdown",
        "text/markdown",
    )


def test_an_unclosed_fence_contributes_no_language():
    """A response truncated mid-stream has no complete block to read."""
    assert classify_media("```python\nx = 1\n") == ("markdown", "text/markdown")


def test_a_second_blocks_language_does_not_override_the_first():
    """The *first* block is the artifact; a later example is not its language."""
    assert classify_media("```python\na=1\n```\n\n```json\n{}\n```\n") == (
        "python",
        "text/x-source",
    )


# ─── the graph node ─────────────────────────────────────────────────────────


class _Msg:
    def __init__(self, content, msg_type="human"):
        self.content = content
        self.type = msg_type


def _state(answer: str, user: str = "how did it go", **over):
    state = {
        "messages": [_Msg(user, "human"), _Msg(answer, "ai")],
        "user_id": "11111111-1111-1111-1111-111111111111",
        "conversation_id": "22222222-2222-2222-2222-222222222222",
        "mode": "normal",
    }
    state.update(over)
    return state


class _FakeArtifact:
    def __init__(self, artifact_id="aaaa", version=1, title="t"):
        self.id = artifact_id
        self.version = version
        self.title = title


class _FakeRepo:
    """Records what the node asked the repository to do."""

    def __init__(self, existing=None):
        self.existing = existing
        self.created: list[dict] = []
        self.bumped: list = []

    async def find_by_conversation_title(self, **kwargs):
        self.find_kwargs = kwargs
        return self.existing

    async def create_artifact(self, payload):
        self.created.append(payload)
        return _FakeArtifact(title=payload["title"])

    async def bump_version(self, artifact, patch):
        self.bumped.append((artifact, patch))
        artifact.version += 1
        return artifact


@pytest.fixture
def repo(monkeypatch):
    """Patch the repository *and* the session factory the node opens."""
    from backend.app.agents.orchestrator import artifact_node as an
    from backend.app.domain.artifact import repository as repo_mod

    fake = _FakeRepo()
    monkeypatch.setattr(repo_mod, "ArtifactRepository", lambda _s: fake)

    import backend.app.infrastructure.database.session as session_mod

    class _Sess:
        async def __aenter__(self):
            return object()

        async def __aexit__(self, *a):
            return False

    monkeypatch.setattr(session_mod, "async_session_factory", lambda: _Sess())
    an._patched = True
    return fake


async def test_the_node_saves_a_document(repo):
    from backend.app.agents.orchestrator.artifact_node import artifact_node

    out = await artifact_node(_state(DOCUMENT))
    assert out["artifact_id"]
    assert out["artifact_title"] == "Incident Review: Checkout Latency"
    assert out["artifact_created"] is True
    assert out["artifact_version"] == 1
    assert len(repo.created) == 1
    assert repo.created[0]["content"] == DOCUMENT


async def test_the_artifact_body_is_the_answer_verbatim(repo):
    """The whole cost argument: no second generation, so no divergence.

    If the canvas and the chat ever disagree about what the assistant said, the
    user has no way to tell which one is the real answer.
    """
    from backend.app.agents.orchestrator.artifact_node import artifact_node

    await artifact_node(_state(DOCUMENT))
    assert repo.created[0]["content"] == DOCUMENT


async def test_a_regeneration_becomes_a_new_version_not_a_duplicate(repo):
    from backend.app.agents.orchestrator.artifact_node import artifact_node

    existing = _FakeArtifact(version=3)
    repo.existing = existing

    out = await artifact_node(_state(DOCUMENT))
    assert not repo.created, "a regeneration created a second artifact"
    assert len(repo.bumped) == 1
    assert out["artifact_created"] is False
    assert out["artifact_version"] == 4


async def test_the_version_lookup_is_scoped_to_user_and_conversation(repo):
    """A same-titled document in another thread must not be hijacked."""
    from backend.app.agents.orchestrator.artifact_node import artifact_node

    await artifact_node(_state(DOCUMENT))
    assert repo.find_kwargs["user_id"].hex == "11111111111111111111111111111111"
    assert repo.find_kwargs["conversation_id"].hex == "22222222222222222222222222222222"
    assert repo.find_kwargs["title"] == "Incident Review: Checkout Latency"


async def test_a_chat_reply_saves_nothing(repo):
    from backend.app.agents.orchestrator.artifact_node import artifact_node

    out = await artifact_node(_state("Sure! The deploy went out at 14:02."))
    assert out == {}
    assert not repo.created


@pytest.mark.parametrize(
    "field", ["user_id", "conversation_id"]
)
async def test_a_missing_identity_saves_nothing(repo, field):
    """An unscoped artifact is a data leak, so refuse rather than guess."""
    from backend.app.agents.orchestrator.artifact_node import artifact_node

    state = _state(DOCUMENT)
    state[field] = ""
    out = await artifact_node(state)
    assert out == {}
    assert not repo.created


async def test_a_malformed_identity_saves_nothing(repo):
    from backend.app.agents.orchestrator.artifact_node import artifact_node

    out = await artifact_node(_state(DOCUMENT, user_id="not-a-uuid"))
    assert out == {}
    assert not repo.created


async def test_an_oversized_answer_is_skipped(repo):
    from backend.app.agents.orchestrator.artifact_node import artifact_node

    out = await artifact_node(_state(DOCUMENT + ("padding. " * 40000)))
    assert out == {}
    assert not repo.created


async def test_an_empty_answer_saves_nothing(repo):
    from backend.app.agents.orchestrator.artifact_node import artifact_node

    out = await artifact_node(_state(""))
    assert out == {}


async def test_a_multimodal_answer_still_produces_text(repo):
    from backend.app.agents.orchestrator.artifact_node import artifact_node

    state = _state("")
    state["messages"] = [
        _Msg("draw this", "human"),
        type(
            "M",
            (),
            {
                "type": "ai",
                "content": [
                    {"type": "text", "text": DOCUMENT},
                    {"type": "image_url", "image_url": {"url": "data:..."}},
                ],
            },
        )(),
    ]
    out = await artifact_node(state)
    assert out["artifact_id"]


async def test_a_database_failure_costs_only_the_artifact(monkeypatch):
    """The user's reply must survive a write failure.

    A graph exception here would cost the entire turn - the answer the user is
    already reading - to save them one file.
    """
    from backend.app.agents.orchestrator import artifact_node as an
    from backend.app.domain.artifact import repository as repo_mod

    class _Broken:
        def __init__(self, _s):
            pass

        async def find_by_conversation_title(self, **kwargs):
            raise RuntimeError("database is down")

    monkeypatch.setattr(repo_mod, "ArtifactRepository", _Broken)
    import backend.app.infrastructure.database.session as session_mod

    class _Sess:
        async def __aenter__(self):
            return object()

        async def __aexit__(self, *a):
            return False

    monkeypatch.setattr(session_mod, "async_session_factory", lambda: _Sess())
    assert an is not None

    out = await an.artifact_node(_state(DOCUMENT))
    assert out == {}, "a failed write must not surface as a state delta"


async def test_the_node_never_raises(monkeypatch):
    """Whatever happens inside, the graph gets a dict back."""
    from backend.app.agents.orchestrator import artifact_node as an

    async def _boom(*a, **k):
        raise RuntimeError("everything is broken")

    monkeypatch.setattr(an, "decide", _boom)
    out = await an.artifact_node(_state(DOCUMENT))
    assert out == {}


# ─── wiring ─────────────────────────────────────────────────────────────────


def test_the_graph_routes_the_synthesized_turn_through_the_artifact_node():
    """After the synthesizer, before END.

    Before the synthesizer the artifact would be built from a draft. After END
    it would never run at all.
    """
    from backend.app.agents.orchestrator.graph import _build_workflow

    compiled = _build_workflow().compile()
    edges = {(e.source, e.target) for e in compiled.get_graph().edges}
    assert ("synthesizer", "artifact") in edges
    assert ("artifact", "__end__") in edges


def test_the_state_declares_the_artifact_fields():
    from backend.app.agents.orchestrator.state import AgentState

    for key in ("artifact_id", "artifact_title", "artifact_version", "artifact_created"):
        assert key in AgentState.__annotations__


def test_the_settings_exist_and_are_sane():
    from backend.app.core.config import settings

    assert settings.ARTIFACT_GENERATION_ENABLED is True
    assert settings.ARTIFACT_MIN_DOCUMENT_CHARS >= 500
    assert settings.ARTIFACT_MIN_CLASSIFIER_CHARS > 0
    assert settings.ARTIFACT_ALLOW_CLASSIFIER is True


class _RecordingSession:
    """Captures the statement a repository method builds.

    The node's tests replace `ArtifactRepository` wholesale, so the query never
    runs and the filter is never exercised -- a revert harness confirmed the
    title predicate could be inverted with nothing turning red. This records the
    compiled statement instead of mocking the method away.
    """

    def __init__(self, result=None):
        self.statement = None
        self._result = result

    async def exec(self, statement):
        self.statement = statement
        return self

    def first(self):
        return self._result

    def all(self):
        return [self._result] if self._result else []


def _uid(value: str):
    from uuid import UUID

    return UUID(value)


async def test_the_version_lookup_filters_on_user_conversation_and_title():
    """All three scopes, not just the title.

    Scoping on title alone would let a document called "Report" in one thread be
    silently revised by an unrelated "Report" in another.
    """
    from backend.app.domain.artifact.repository import ArtifactRepository

    session = _RecordingSession()
    await ArtifactRepository(session).find_by_conversation_title(
        user_id=_uid("11111111-1111-1111-1111-111111111111"),
        conversation_id=_uid("22222222-2222-2222-2222-222222222222"),
        title="Q3 Report",
    )
    sql = str(session.statement)
    for fragment in ("user_id", "conversation_id", "title"):
        assert fragment in sql, f"{fragment} is not part of the lookup: {sql}"

    # Values arrive as bind parameters, not interpolated literals -- asserting
    # on the rendered string alone would pass even if the wrong value were bound.
    bound = list(session.statement.compile().params.values())
    assert "Q3 Report" in bound
    assert _uid("11111111-1111-1111-1111-111111111111") in bound
    assert _uid("22222222-2222-2222-2222-222222222222") in bound


def _comparison_operators(statement) -> list:
    """Every comparison operator in a statement's WHERE clause."""
    from sqlalchemy.sql.elements import BinaryExpression

    found: list = []
    stack = [statement.whereclause]
    while stack:
        current = stack.pop()
        if isinstance(current, BinaryExpression):
            found.append(current.operator)
        stack.extend(current.get_children())
    return found


async def test_the_version_lookup_asks_for_equality_not_exclusion():
    """The one property the bind-parameter assertions cannot see.

    A revert harness caught this: with the title filter inverted from ``==`` to
    ``!=``, the rendered SQL still names the same three columns and still binds
    the same three values, so both assertions in the test above stayed green.
    Only the *operator* distinguishes "the artifact I am revising" from "every
    artifact except that one", and only the operator is asserted here.
    """
    from backend.app.domain.artifact.repository import ArtifactRepository
    from sqlalchemy.sql import operators

    session = _RecordingSession()
    await ArtifactRepository(session).find_by_conversation_title(
        user_id=_uid("11111111-1111-1111-1111-111111111111"),
        conversation_id=_uid("22222222-2222-2222-2222-222222222222"),
        title="Q3 Report",
    )
    found = _comparison_operators(session.statement)
    assert found, "the lookup has no comparisons to inspect"
    for operator in found:
        assert operator is operators.eq, f"lookup uses {operator}, not equality"


async def test_the_version_lookup_returns_the_most_recent_match():
    from backend.app.domain.artifact.repository import ArtifactRepository

    existing = _FakeArtifact(version=2)
    session = _RecordingSession(result=existing)
    found = await ArtifactRepository(session).find_by_conversation_title(
        user_id=_uid("11111111-1111-1111-1111-111111111111"),
        conversation_id=_uid("22222222-2222-2222-2222-222222222222"),
        title="Q3 Report",
    )
    assert found is existing
    assert "ORDER BY" in str(session.statement).upper()


def test_the_repository_lookup_exists():
    from backend.app.domain.artifact.repository import ArtifactRepository

    assert hasattr(ArtifactRepository, "find_by_conversation_title")


def test_the_run_event_frames_helper_is_used_by_the_route():
    """The route must not re-inline the frames it delegated.

    A source-grep is weak on its own -- an earlier wiring test in this repo
    passed even with the call's result discarded -- so this only asserts the
    delegation exists; `tests/test_batch_d3_run_events.py` is what actually
    pins the emitted frames.
    """
    from pathlib import Path

    source = (
        Path(__file__).resolve().parents[1]
        / "app"
        / "api"
        / "v1"
        / "conversations.py"
    ).read_text(encoding="utf-8")
    assert "finished_run_events(snapshot.values)" in source
    # The frames must no longer be built inline here.
    assert "'type': 'artifact'" not in source
