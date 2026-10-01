"""
Should this turn's answer become a durable artifact?

The artifact tables, the versioning service, the canvas and the saved-artifacts
list all already exist. What was missing is any way for a chat turn to *end up
as one of them*: only ``POST /artifacts`` and the MCP tool could create an
artifact, so the user had to notice a document had scrolled past, then ask for
it out of band. This module is that missing decision.

Design, and the reasoning behind the order
------------------------------------------

Cheapest and most certain first, and an LLM only as a last resort:

1. **Explicit request** (``_explicit_request``) -- free. The user said "write me
   a report" or typed ``/doc``. Certain when it fires.
2. **Structural** (``_structural_signal``) -- free. The answer already *is* a
   document: long, and organised with headings or a substantial code block.
   This is the strongest free signal there is, because it inspects the thing
   that actually matters (the output) rather than the request.
3. **Classifier** (``_classify``) -- one small typed call, only when 1 and 2 are
   both silent *and* the run's spend meter has headroom.

The order matters for cost: two of the three layers cost nothing, and the
expensive one runs only on genuinely ambiguous turns.

Bias: **toward creating**
------------------------

A false positive costs the user one stray row and one click to delete it. A
false negative costs them the document itself -- it stays buried in chat
scroll, and re-asking costs another full generation to get text they already
had. Asymmetric, so the threshold leans toward yes. (Same reasoning as the
memory write gate in ``memory_lifecycle.py``: a missed write is permanent, a
redundant one is deduplicated downstream.)

Why the answer is never regenerated
-----------------------------------

The obvious design -- detect the intent, then call the model to *generate* the
artifact -- is wrong here, and paying for it would be the expensive mistake in
this feature. The synthesizer has already written the text; regenerating it
would cost a second full generation to produce *different* text, leaving the
canvas and the chat reply permanently disagreeing about what the assistant
said. So the artifact body is the synthesizer's ``response_text``, verbatim,
and this module derives only the metadata around it (title, language, mime
type) -- all deterministically and for free.

That makes the whole feature cost *at most* one small classification call, on
ambiguous turns only. For reference, open-canvas (the one repo of the five
surveyed with a real artifact system) spends two unbudgeted model calls per
artifact: a router call plus a forced full generation
(``generate-path/dynamic-determine-path.ts:62-88``,
``generate-artifact/index.ts:34-45``), with no spend ceiling on either.

The one deliberate borrowing from that repo is the *narrowed schema*: its
router enum is narrowed by current state so it structurally cannot choose
"generate new" when an artifact already exists
(``dynamic-determine-path.ts:62-64``). Here the classifier's response model
has a single boolean field, so it cannot express a title, a language, a
rewrite, or anything else -- the same guarantee, obtained by making the schema
incapable of overreach instead of asking the model politely in prose.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass

from pydantic import BaseModel, Field

#: Nouns that make "write a <noun>" a document request rather than a question.
_DOCUMENT_NOUNS = (
    "document", "doc", "report", "spec", "specification", "guide", "article",
    "blog post", "readme", "cheatsheet", "cheat sheet", "tutorial", "proposal",
    "design doc", "rfc", "api reference", "changelog", "release notes",
    "resume", "cv", "cover letter", "letter", "email", "memo", "whitepaper",
    "runbook", "checklist", "roadmap", "transcript", "outline", "summary doc",
)

#: Verb + determiner that must precede the noun for an explicit request to
#: count. Deliberately narrow: "explain the architecture document" is a
#: question about a document, not a request for one.
_REQUEST_VERBS = (
    "write", "create", "generate", "draft", "produce", "compose", "build",
    "make", "prepare", "put together", "put together a",
)

_SLASH_COMMAND = re.compile(
    r"(?:^|\s)/(doc|artifact|file|document|report|spec)\b", re.IGNORECASE
)

#: "write me a report", "draft a spec", ... The determiner is required so that
#: "write code to sort a list" is not read as a document request.
_EXPLICIT_REQUEST = re.compile(
    r"\b(" + "|".join(re.escape(v) for v in _REQUEST_VERBS) + r")\s+"
    r"(?:me\s+)?(?:a|an|the|some)\s+"
    r"(?:" + "|".join(re.escape(n) for n in _DOCUMENT_NOUNS) + r")\b",
    re.IGNORECASE,
)

#: A markdown heading, capturing its full text. The text is captured rather
#: than left to a later match because a non-capturing `\S` would match only the
#: heading's first character -- which is what this originally did, silently
#: producing single-letter artifact titles.
_HEADING = re.compile(r"^#{1,3}\s+(\S.*)$", re.MULTILINE)
_FENCE = re.compile(r"^```", re.MULTILINE)


@dataclass(frozen=True)
class ArtifactIntent:
    """The decision, plus why it was made.

    ``reason`` is a stable identifier, not prose: it goes into the log line and
    the revert-check assertions, so it must not drift with wording.
    """

    create: bool
    reason: str
    title: str = ""
    language: str = "markdown"
    mime_type: str = "text/markdown"
    #: True when an LLM call was made. Kept so the test suite can assert that
    #: the common case spends nothing.
    used_model: bool = False


# ─── Layer 1: explicit request (free) ────────────────────────────────────────


def _explicit_request(user_text: str, mode: str) -> str:
    """Return a reason if the user plainly asked for a file, else ``""``.

    ``mode == "code"`` short-circuits to yes. Code mode exists to produce a
    runnable thing; a code answer delivered only as chat text is the one case
    where a file is unambiguously the right output, and asking a model about it
    would be paying to be told what the mode already said.
    """
    if mode == "code":
        return "code_mode"
    if not user_text:
        return ""
    if _SLASH_COMMAND.search(user_text):
        return "slash_command"
    if _EXPLICIT_REQUEST.search(user_text):
        return "explicit_request"
    return ""


# ─── Layer 2: structural signal (free) ───────────────────────────────────────


def _fenced_blocks(answer: str) -> list[tuple[str, str]]:
    """Every *closed* fenced block, as ``(declared_language, content)``.

    One parser for all three places that need fences -- the structural signal,
    the media classifier, and the title deriver -- because three regex passes
    over the same text is three chances to disagree about where a block starts.
    An unclosed fence is ignored: a response truncated mid-stream has no
    artifact to offer.
    """
    blocks: list[tuple[str, str]] = []
    lines = answer.splitlines()
    index = 0
    while index < len(lines):
        if not lines[index].strip().startswith("```"):
            index += 1
            continue
        declared = lines[index].strip()[3:].strip().lower()
        body: list[str] = []
        index += 1
        closed = False
        while index < len(lines):
            if lines[index].strip().startswith("```"):
                closed = True
                index += 1
                break
            body.append(lines[index])
            index += 1
        if closed:
            blocks.append((declared, "\n".join(body)))
    return blocks


def _structural_signal(
    answer: str, min_chars: int, min_code_chars: int, min_code_share: float
) -> str:
    """Return a reason if the answer already has the shape of a document.

    Length alone is not enough -- a long chat reply is still a chat reply. What
    distinguishes a document is that it is *organised*: headings, or code that
    is the substance of the answer rather than an illustration inside it.

    The code test is *proportion*, not fence count, and that distinction is the
    whole ballgame. "Use `%` to test for evenness: ```x % 2 == 0```" has a
    perfectly closed fence and is still a chat answer -- turning it into a
    source file is the kind of false positive that makes a feature like this
    intolerable. Requiring the code to be a real fraction of the answer, or to
    run to real length, keeps that case out while still catching a forty-line
    function delivered with a sentence of framing.

    The two signals also have separate length floors: a prose document is long
    *because* it is a document, and a code file can be worth keeping well
    before it reaches prose length.
    """
    if not answer:
        return ""

    blocks = _fenced_blocks(answer)
    if blocks:
        code_chars = sum(len(body) for _lang, body in blocks)
        code_lines = sum(
            1 for _lang, body in blocks for line in body.splitlines() if line.strip()
        )
        is_substantial = code_chars >= min_code_chars or code_lines >= 10
        is_dominant = code_chars / max(len(answer), 1) >= min_code_share
        if is_substantial and is_dominant:
            return "code_block"

    if len(answer) >= min_chars and len(_HEADING.findall(answer)) >= 2:
        return "document_headings"

    return ""


# ─── Layer 3: classifier (one small typed call, last resort) ─────────────────


class _ArtifactVerdict(BaseModel):
    """Single-field by design.

    The response model is the whole safety mechanism: it cannot express a
    title, a language, a rewrite or a format, so a hallucinating model cannot
    talk the pipeline into doing something the decision did not authorise.
    Borrowed from open-canvas's narrowed router enum
    (``dynamic-determine-path.ts:62-64``), which gets the same guarantee at the
    schema layer rather than through prompt pleading.
    """

    is_durable_artifact: bool = Field(
        description=(
            "True only if the user asked for something that should be kept as a "
            "standalone file: a document, report, spec, guide, or a piece of "
            "runnable code. False for ordinary questions, explanations, "
            "clarifications, and short factual answers."
        )
    )


_CLASSIFIER_SYSTEM = (
    "You decide whether a chat reply should be saved as a standalone file "
    "artifact instead of being left in the chat.\n\n"
    "Answer true for: a document, report, specification, design doc, guide, "
    "tutorial, article, readme, changelog, runbook, letter, email draft, or a "
    "piece of runnable code the user asked you to produce.\n"
    "Answer false for: explanations, answers to questions, comparisons, "
    "clarifications, greetings, or anything conversational.\n\n"
    "When it is genuinely unclear, answer false only if the reply is short or "
    "conversational; if the user asked for something substantial and document-"
    "shaped, answer true."
)


async def _classify(user_text: str, answer: str) -> str:
    """Ask the model, once, whether this is durable. Returns a reason or ``""``.

    Returns ``""`` on any failure. A classifier that cannot be reached must not
    decide anything -- it is a tie-breaker between two free layers, so
    declining to vote leaves the turn without an artifact, which is the
    recoverable direction.
    """
    try:
        # Imported here, and by name verified against the module: the
        # singleton is `structured_service`, not `structured_output_service`.
        # A wrong name raises ImportError, which the handler below swallows --
        # and a silently dead classifier layer looks exactly like a classifier
        # that decided "no" every time. This is the html2text trap: an import
        # that never resolves, hidden by a broad except.
        from backend.app.services.structured_output import structured_service

        verdict = await structured_service.generate_structured(
            response_model=_ArtifactVerdict,
            messages=[
                {"role": "system", "content": _CLASSIFIER_SYSTEM},
                {
                    "role": "user",
                    "content": (
                        f"User request:\n{user_text[:2000]}\n\n"
                        f"Assistant reply:\n{answer[:2000]}"
                    ),
                },
            ],
            # One attempt. Instructor's default is 3 re-prompts on validation
            # failure, and a tie-breaker call is not worth 3x its cost.
            max_retries=1,
        )
    except Exception:
        # Deliberately silent. This layer is optional by construction; a log
        # line per ambiguous turn would be noise, and the two free layers have
        # already had their say.
        return ""
    return "classifier" if getattr(verdict, "is_durable_artifact", False) else ""


# ─── Metadata derivation (free, deterministic) ───────────────────────────────


def derive_title(answer: str, user_text: str) -> str:
    """A stable, human-meaningful title. Never a model call.

    The title is the artifact's identity for versioning (see
    ``artifact_node._persist``), so it has to be *deterministic*: the same
    request regenerated twice must land on the same title, or the version
    history never links up and the user gets duplicates instead of a history.
    """
    for match in _HEADING.finditer(answer):
        title = match.group(1).strip()
        if title:
            return title[:200]

    # Fence bodies are skipped by tracking the marker, not by consulting
    # `_fenced_blocks`, because that helper only reports *closed* blocks while
    # this needs to stop at an unclosed one too: a truncated response should not
    # have its last line used as a title. Skipping bodies matters because lines
    # inside a fence look like ordinary text -- a leading `x = 1` is the first
    # "prose" line of a code answer, and titling an artifact after its first
    # statement is worse than titling it after the question.
    in_fence = False
    for line in answer.splitlines():
        stripped = line.strip()
        if stripped.startswith("```"):
            in_fence = not in_fence
            continue
        if in_fence or not stripped:
            continue
        # Skip structural markup, which is not a sentence anyone would title a
        # document with.
        if stripped.startswith(("-", "*", "|", ">", "#!", "=")):
            continue
        return stripped[:200]

    fallback = " ".join((user_text or "").split())[:200]
    return fallback or "Untitled artifact"


def classify_media(answer: str) -> tuple[str, str]:
    """Infer ``(language, mime_type)`` from the content's own shape.

    A fenced block that *declares* a language is the only reliable signal, and
    it is deliberately the only one. A bare fence has no language to name, and
    guessing one ("python" because the block has colons) produces a file the
    editor then opens with the wrong syntax highlighting -- a worse outcome than
    plainly calling it markdown.

    Note there is no ``mode`` parameter, though one was here first. Code mode
    does imply the answer is source, but it does not imply *which* language, and
    an unnamed ``text/x-source`` artifact is not more useful than a named
    markdown one.
    """
    for declared, _body in _fenced_blocks(answer):
        if declared and re.fullmatch(r"[a-z0-9+#.\-]{1,30}", declared):
            return declared, "text/x-source"
    return "markdown", "text/markdown"


# ─── The decision ────────────────────────────────────────────────────────────


@dataclass
class IntentConfig:
    enabled: bool = True
    min_document_chars: int = 1200
    #: Separate, lower floor for the code-block signal. See `_structural_signal`.
    min_code_chars: int = 400
    min_classifier_chars: int = 200
    #: How much of the answer must be code for a code artifact. See
    #: `_structural_signal` -- this is the single most important tunable in the
    #: module, because it is what separates "here is a file" from "here is a
    #: one-line snippet inside an explanation".
    min_code_share: float = 0.25
    #: Whether the LLM tie-breaker may run at all. Separated from ``enabled``
    #: so the free layers can be evaluated in production with the paid layer
    #: off, which is how the thresholds above were tuned.
    allow_classifier: bool = True


async def decide(
    *,
    user_text: str,
    answer: str,
    mode: str,
    config: IntentConfig | None = None,
    can_spend: Callable[[], bool] | None = None,
) -> ArtifactIntent:
    """Decide whether ``answer`` should become an artifact.

    ``can_spend`` is an optional predicate supplied by the caller (the run's
    spend meter). When it says no, the classifier layer is skipped and the free
    layers' verdict stands. A turn that has already hit its budget does not get
    a new optional call.
    """
    cfg = config or IntentConfig()

    if not cfg.enabled:
        return ArtifactIntent(create=False, reason="disabled")
    if not (answer or "").strip():
        return ArtifactIntent(create=False, reason="empty_answer")

    reason = _explicit_request(user_text or "", mode)
    if not reason:
        reason = _structural_signal(
            answer,
            cfg.min_document_chars,
            cfg.min_code_chars,
            cfg.min_code_share,
        )

    used_model = False
    if not reason:
        # Only a substantial, non-conversational reply is worth a call. A
        # two-line answer is not an artifact under any reading, and asking
        # about it is pure waste.
        if cfg.allow_classifier and len(answer) >= cfg.min_classifier_chars:
            if can_spend is None or can_spend():
                reason = await _classify(user_text or "", answer)
                used_model = bool(reason)

    if not reason:
        return ArtifactIntent(create=False, reason="no_signal", used_model=used_model)

    language, mime_type = classify_media(answer)
    return ArtifactIntent(
        create=True,
        reason=reason,
        title=derive_title(answer, user_text),
        language=language,
        mime_type=mime_type,
        used_model=used_model,
    )
