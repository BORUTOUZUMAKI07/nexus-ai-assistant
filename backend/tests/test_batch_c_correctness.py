"""
Tests for Batch C - confidence action, scrape content-shape guards,
credential validation and the requires_scraping contract.

Each test fails if its fix is reverted.
"""
import asyncio
from contextlib import asynccontextmanager

import structlog
from backend.app.core.credential_check import (
    CREDENTIAL_NAMES,
    ValidationReport,
    format_report,
    looks_like_placeholder,
    validate_credential,
    validate_settings,
)
from backend.app.services.confidence_action import (
    NEVER_HEDGE_BELOW,
    apply_confidence_action,
    should_hedge,
)
from backend.app.services.rag.base import (
    FULL_TEXT_CHARS,
    REQUIRES_SCRAPING,
    mark_requires_scraping,
    needs_scraping,
)
from backend.app.services.tools.content_shape import (
    MIN_USEFUL_CHARS,
    classify_content,
    html_to_text,
    salvage_sentences,
    scrape_result_verdict,
)

GOOD_PAGE = (
    "Retrieval-augmented generation combines a parametric model with a "
    "non-parametric retriever. The retriever returns passages that are "
    "concatenated to the prompt before generation. This approach reduces "
    "hallucination because the model can attend to source text. It was first "
    "described in the 2020 paper on knowledge-intensive NLP tasks. The result "
    "is a system that cites its sources and can be evaluated for faithfulness. "
) * 3


# -- C1: act on the confidence gate -------------------------------------------


def _decision(confidence: float, action: str, grounded: bool = True) -> dict:
    return {"confidence": confidence, "action": action, "grounded": grounded}


def test_low_confidence_grounded_answer_is_annotated():
    result = apply_confidence_action("The answer is 42.", _decision(0.3, "hedge"))
    assert result.hedged
    assert "42" in result.text
    assert "Low confidence" in result.text
    assert "30%" in result.text


def test_high_confidence_answer_is_untouched():
    text = "The answer is 42, and here is why."
    result = apply_confidence_action(text, _decision(0.95, "answer"))
    assert not result.hedged
    assert result.text == text
    assert result.reason == "gate_says_answer"


def test_ungrounded_casual_chat_is_not_hedged():
    """ConfidenceService scores ungrounded chat 0.95/"answer" on purpose.

    Hedging "Hello! How can I help?" would be absurd, and hedging it often
    trains users to ignore the signal entirely.
    """
    result = apply_confidence_action(
        "Hello! How can I help you today?",
        _decision(0.95, "answer", grounded=False),
    )
    assert not result.hedged
    assert result.reason == "gate_says_answer"


def test_broken_gate_score_does_not_hedge_everything():
    """Below the floor the gate is assumed broken, not the text untrustworthy."""
    result = apply_confidence_action("Some text.", _decision(0.01, "hedge"))
    assert not result.hedged
    assert result.reason == "below_hedge_floor"


def test_low_confidence_ungrounded_is_still_hedged():
    result = apply_confidence_action(
        "I think the migration finished last week.",
        _decision(0.4, "hedge", grounded=False),
    )
    assert result.hedged
    assert result.reason == "low_confidence_ungrounded:annotate"
    assert "Unverified" in result.text


def test_already_hedged_answer_is_not_annotated_twice():
    """Double hedging reads as a model malfunction, not as caution."""
    text = "I'm not certain about this, but it may be related to the config."
    result = apply_confidence_action(text, _decision(0.3, "hedge"))
    assert not result.hedged
    assert result.reason == "already_hedged"
    assert result.text == text


def test_question_is_not_treated_as_a_factual_claim():
    result = apply_confidence_action("What database should I use?", _decision(0.3, "hedge"))
    assert not result.hedged
    assert result.reason == "not_a_factual_claim"


def test_abstain_mode_replaces_the_answer():
    result = apply_confidence_action(
        "The answer is 42.", _decision(0.3, "hedge"), mode="abstain"
    )
    assert result.hedged
    assert "42" not in result.text
    assert "confident enough" in result.text
    assert result.reason == "below_confidence_threshold:abstain"


def test_mode_none_records_only():
    result = apply_confidence_action(
        "The answer is 42.", _decision(0.3, "hedge"), mode="none"
    )
    assert not result.hedged
    assert result.reason == "hedge_mode_none"
    assert result.text == "The answer is 42."


def test_missing_decision_fails_open():
    for bad in (None, {}, {"action": "hedge"}, {"confidence": None, "action": "hedge"}):
        result = apply_confidence_action("The answer is 42.", bad)
        assert not result.hedged
        assert result.text == "The answer is 42."


def test_non_numeric_confidence_fails_open():
    result = apply_confidence_action("Text.", {"confidence": "high", "action": "hedge"})
    assert not result.hedged
    assert result.reason == "non_numeric_confidence"


def test_empty_response_does_not_crash():
    result = apply_confidence_action("", _decision(0.3, "hedge"))
    assert isinstance(result.hedged, bool)


def test_hedge_is_idempotent_on_reentry():
    """A response that re-enters the gate must not collect a second note.

    This is reachable: the synthesizer's critic revision loop re-synthesises
    from the prior draft, and a resumed run re-reads the same state.
    """
    once = apply_confidence_action("The answer is 42.", _decision(0.3, "hedge"))
    twice = apply_confidence_action(once.text, _decision(0.3, "hedge"))
    assert not twice.hedged
    assert twice.text == once.text
    assert once.text.count("Low confidence") == 1


def test_should_hedge_returns_verdict_then_reason():
    """The reason is streamed to the UI, so it must be a real string."""
    verdict, reason = should_hedge(None)
    assert verdict is False
    assert isinstance(reason, str) and reason

    verdict, reason = should_hedge(_decision(0.3, "hedge"))
    assert verdict is True
    assert isinstance(reason, str) and reason


def test_floor_is_below_any_usable_confidence():
    assert 0.0 < NEVER_HEDGE_BELOW < 0.3


# -- C1 wiring: the synthesizer branches on the verdict -------------------------


class _Msg:
    def __init__(self, content: str, msg_type: str = "human") -> None:
        self.content = content
        self.type = msg_type


class _NoCritic:
    async def evaluate(self, **kwargs):
        return {"approved": True, "critique": ""}


def _synth_state(user_text: str = "Tell me about X"):
    return {
        "messages": [_Msg(user_text, "human")],
        "user_id": "",
        "conversation_id": "c1",
        "trace_id": "",
        "mode": "normal",
        "system_prompt": "SYSTEM",
        "task_type": "general",
        "revision_count": 0,
        "citations": [],
    }


async def _run(monkeypatch, decision):
    """Drive synthesizer_node with the confidence gate forced to `decision`.

    The gate is imported inside the node body, so the seam to patch is the
    singleton's method, not a module-level name in nodes.
    """
    from backend.app.agents.orchestrator import nodes
    from backend.app.services.confidence_service import confidence_service

    async def fake_completion(**kwargs):
        return "Paris is the capital of France, a well established fact."

    monkeypatch.setattr(nodes.ai_client, "completion", fake_completion)
    monkeypatch.setattr(nodes, "critic_subagent", _NoCritic())
    monkeypatch.setattr(confidence_service, "decide", lambda *a, **k: decision)
    return await nodes.synthesizer_node(_synth_state())


async def test_synthesizer_annotates_a_low_confidence_answer(monkeypatch):
    """The regression: the verdict was computed and nothing branched on it."""
    out = await _run(
        monkeypatch,
        {"confidence": 0.25, "action": "hedge", "grounded": True, "threshold": 0.6},
    )
    assert "Low confidence" in out["messages"][0].content


async def test_synthesizer_leaves_a_confident_answer_alone(monkeypatch):
    out = await _run(
        monkeypatch,
        {"confidence": 0.95, "action": "answer", "grounded": True, "threshold": 0.6},
    )
    text = out["messages"][0].content
    assert "Low confidence" not in text
    assert "Paris" in text


async def test_synthesizer_survives_the_action_throwing(monkeypatch):
    """Fail-open: the hedge must never cost the user their answer."""
    from backend.app.agents.orchestrator import nodes

    def boom(*a, **k):
        raise RuntimeError("hedge exploded")

    monkeypatch.setattr(nodes, "apply_confidence_action", boom)
    out = await _run(
        monkeypatch,
        {"confidence": 0.25, "action": "hedge", "grounded": True, "threshold": 0.6},
    )
    assert "Paris" in out["messages"][0].content
    assert "Low confidence" not in out["messages"][0].content


async def test_synthesizer_respects_the_disable_switch(monkeypatch):
    from backend.app.agents.orchestrator import nodes

    monkeypatch.setattr(nodes.settings, "CONFIDENCE_ACTION_ENABLED", False)
    out = await _run(
        monkeypatch,
        {"confidence": 0.1, "action": "hedge", "grounded": True, "threshold": 0.6},
    )
    assert "Low confidence" not in out["messages"][0].content


# -- C2: scrape content-shape guards -------------------------------------------


def test_real_page_passes():
    verdict = classify_content(GOOD_PAGE)
    assert verdict.ok
    assert verdict.reason == ""


def test_cloudflare_block_page_is_rejected():
    body = (
        "<html><head><title>Just a moment...</title></head><body>"
        "Checking your browser before accessing. Please enable JavaScript and "
        "cookies to continue. Ray ID: 8f3a2b1c</body></html>"
    )
    verdict = classify_content(body)
    assert not verdict.ok
    assert verdict.reason == "block_page"


def test_access_denied_is_rejected():
    verdict = classify_content(
        "Access Denied - You don't have permission to access this page."
    )
    assert not verdict.ok
    assert verdict.reason == "block_page"


def test_rate_limit_notice_is_rejected():
    verdict = classify_content("Rate limit exceeded. Please retry your request in 60 seconds.")
    assert not verdict.ok
    assert verdict.reason == "block_page"


def test_javascript_required_shell_is_rejected():
    verdict = classify_content("Please enable JavaScript to view this site in your browser.")
    assert not verdict.ok


def test_unprocessed_pdf_is_rejected():
    verdict = classify_content("%PDF-1.7\n1 0 obj\n<< /Type /Catalog >>\nendobj\n" + "\x00" * 50)
    assert not verdict.ok
    assert verdict.reason in ("unprocessed_pdf", "binary_content")


def test_pdf_content_type_is_rejected():
    verdict = classify_content(GOOD_PAGE, content_type="application/pdf")
    assert not verdict.ok
    assert verdict.reason == "unprocessed_pdf"


def test_mojibake_binary_body_is_rejected():
    """Bytes that were never text: C0 control codes mistaken for characters.

    This is what a failed PDF extraction or a mis-decoded charset actually
    looks like, and it is distinct from a page written in a non-Latin script.
    """
    body = "".join(chr(0x01 + (i % 30)) for i in range(600))
    verdict = classify_content(body)
    assert not verdict.ok
    assert verdict.reason == "undecodable_text"


def test_real_cjk_prose_is_accepted():
    """The counter-test that keeps the allowlist honest."""
    body = "检索增强生成将检索器与参数模型结合使用。这种方法可以减少幻觉。系统会引用来源。" * 8
    verdict = classify_content(body)
    assert verdict.ok, f"real CJK prose rejected: {verdict.reason}"


def test_navigation_shell_is_rejected():
    verdict = classify_content("Loading...")
    assert not verdict.ok
    assert verdict.reason == "navigation_shell"


def test_empty_and_markup_only_are_rejected():
    assert not classify_content("").ok
    assert not classify_content(None).ok
    assert classify_content("   ").reason == "empty_content"
    assert not classify_content("<html><body></body></html>").ok


def test_short_but_real_text_is_rejected_by_length_not_shape():
    verdict = classify_content("Paris is the capital of France.")
    assert not verdict.ok
    assert verdict.reason == "too_short"


def test_block_marker_in_a_long_article_is_not_a_false_positive():
    """A long article quoting '403 Forbidden' is not a block page.

    Without the length guard, any article about web security would be
    discarded, which is worse than the bug being fixed.
    """
    article = (
        "A 403 Forbidden response tells the client the server understood the "
        "request but refuses to authorise it. "
    ) * 30
    verdict = classify_content(article)
    assert verdict.ok


def test_curly_quotes_and_dashes_are_not_mojibake():
    text = (
        "The cafe's \u201cresume\u201d \u2014 its first draft \u2014 is not binary "
        "noise. It is ordinary prose with typographic punctuation, and it "
        "should pass the shape check without complaint. "
    ) * 4
    verdict = classify_content(text)
    assert verdict.ok, f"correctly rejected real prose: {verdict.reason}"


def test_salvage_keeps_readable_lines():
    body = (
        "This is a readable sentence about the topic at hand, long enough to keep.\n"
        "\x00\x01\x02\x03\x04\x05\x06\x07\x08\x09\x0a\x0b\x0c\x0d\x0e\x0f"
        "\x10\x11\x12\x13\x14\x15\x16\x17\x18\x19\x1a\x1b\x1c\x1d\x1e\x1f"
        " garbage line here\n"
        "Another readable sentence with real content worth preserving for later use.\n"
    )
    out = salvage_sentences(body)
    assert "readable sentence" in out
    assert "garbage line" not in out


def test_salvage_returns_empty_when_nothing_survives():
    assert salvage_sentences("") == ""
    assert salvage_sentences("short") == ""


def test_scrape_result_verdict_handles_malformed_input():
    assert not scrape_result_verdict(None).ok
    assert not scrape_result_verdict("a string").ok
    failed = scrape_result_verdict({"success": False, "content": "Unable to scrape"})
    assert not failed.ok
    assert failed.reason == "scrape_reported_failure"


def test_scrape_result_verdict_passes_a_good_result():
    assert scrape_result_verdict(
        {"success": True, "url": "https://x", "content": GOOD_PAGE}
    ).ok


def test_min_useful_chars_is_a_sane_floor():
    assert 100 <= MIN_USEFUL_CHARS <= 2000


BLOCK_PAGE_HTML = (
    "<html><head><title>Just a moment...</title></head><body>"
    "<h1>Checking your browser</h1>"
    "<p>Please enable JavaScript and cookies to continue.</p>"
    "</body></html>"
)


def _scrape_service(monkeypatch, *, firecrawl_key=None, tavily_key=None):
    """A service pinned to the direct-HTTP path.

    Note the empty string rather than None: the constructor treats None as
    "not supplied" and falls back to settings, so passing None would still
    take the Firecrawl branch when a key happens to be configured.
    """
    from backend.app.services.tools.web_search import WebSearchService

    return WebSearchService(
        tavily_key=tavily_key or "",
        firecrawl_key=firecrawl_key if firecrawl_key is not None else "",
    )


def _patch_httpx_fetch(monkeypatch, body: str, content_type: str = "text/html"):
    """Replace the SSRF-guarded fetch that the httpx fallback uses."""
    from backend.app.services.tools import web_search as ws

    class FakeResponse:
        status_code = 200
        headers = {"content-type": content_type}

        def raise_for_status(self):
            return None

    async def _fake_fetch(target: str):
        return 200, target, body.encode("utf-8", errors="replace")

    monkeypatch.setattr(ws, "_fetch_with_ssrf_guard", _fake_fetch)

    async def _allow(target: str) -> str:
        return target

    monkeypatch.setattr(ws, "_validate_public_url", _allow)


async def test_scrape_url_rejects_a_block_page_served_as_200(monkeypatch):
    """The regression, end to end.

    A Cloudflare interstitial returns HTTP 200 with a short body. Before the
    guard it came back as success=True and was forwarded to the model as
    evidence, where "Checking your browser" reads as content about the topic.
    """
    _patch_httpx_fetch(monkeypatch, BLOCK_PAGE_HTML)
    service = _scrape_service(monkeypatch)

    result = await service.scrape_url("https://example.com/article")

    assert result["success"] is False
    assert result["rejection_reason"] == "block_page"
    assert "Unable to scrape" in result["content"]


async def test_scrape_url_accepts_a_real_page(monkeypatch):
    """The counter-test: a normal article must still come back intact."""
    import html as html_mod

    _patch_httpx_fetch(
        monkeypatch, f"<html><body><p>{html_mod.escape(GOOD_PAGE)}</p></body></html>"
    )
    service = _scrape_service(monkeypatch)

    result = await service.scrape_url("https://example.com/article")

    assert result["success"] is True
    assert "retrieval" in result["content"].lower()
    assert "rejection_reason" not in result


async def test_scrape_url_rejects_an_unprocessed_pdf(monkeypatch):
    _patch_httpx_fetch(monkeypatch, "%PDF-1.4\ntrailer\n" + "\x00" * 200, "application/pdf")
    service = _scrape_service(monkeypatch)

    result = await service.scrape_url("https://example.com/paper.pdf")

    assert result["success"] is False
    assert result["rejection_reason"] in ("unprocessed_pdf", "binary_content")


async def test_scrape_url_rejects_an_unextracted_pdf_over_http(monkeypatch):
    """The shape is wrong, not the length, so a length check cannot catch it."""
    _patch_httpx_fetch(monkeypatch, "%PDF-1.7\n1 0 obj\n<< /Type /Catalog >>\nendobj\n")
    service = _scrape_service(monkeypatch)

    result = await service.scrape_url("https://example.com/doc.pdf")

    assert result["success"] is False
    assert result["rejection_reason"] == "unprocessed_pdf"


def _patch_firecrawl(monkeypatch, markdown: str, title: str = "Doc"):
    """Install a fake `firecrawl` module whose scrape returns `markdown`."""
    import types

    class _Result:
        def __init__(self) -> None:
            self.markdown = markdown
            self.metadata = {"title": title}

    class FakeApp:
        def __init__(self, api_key=None, **kwargs):
            self.api_key = api_key

        def scrape_url(self, url, formats=None):
            return _Result()

    module = types.ModuleType("firecrawl")
    module.FirecrawlApp = FakeApp
    monkeypatch.setitem(__import__("sys").modules, "firecrawl", module)


async def test_scrape_url_falls_back_when_firecrawl_returns_a_block_page(monkeypatch):
    """The primary provider is challenged, the fallback is not.

    Both paths are guarded independently, so a block page from either one must
    not become evidence. Here the httpx fallback is patched to return a real
    page, which proves the fallthrough actually happens.
    """
    _patch_firecrawl(monkeypatch, "Just a moment... Checking your browser before accessing.")
    _patch_httpx_fetch(
        monkeypatch, f"<html><body><p>{GOOD_PAGE}</p></body></html>"
    )
    service = _scrape_service(monkeypatch, firecrawl_key="fc_real_key_value_here")

    result = await service.scrape_url("https://example.com/article")

    assert result["success"] is True
    assert "retrieval" in result["content"].lower()


async def test_scrape_url_rejects_a_block_page_from_both_providers(monkeypatch):
    """With no usable path left, the result is an honest failure."""
    _patch_firecrawl(monkeypatch, "Just a moment... Checking your browser before accessing.")
    _patch_httpx_fetch(monkeypatch, BLOCK_PAGE_HTML)
    service = _scrape_service(monkeypatch, firecrawl_key="fc_real_key_value_here")

    result = await service.scrape_url("https://example.com/article")

    assert result["success"] is False
    assert result["rejection_reason"] == "block_page"


async def test_scrape_url_salvages_a_partially_readable_firecrawl_page(monkeypatch):
    """A body with a bad header but readable paragraphs still carries evidence.

    Discarding it would turn the guard into a source of information loss, which
    is the failure mode the researcher's snippet fallback exists to cover.
    """
    mixed = (
        "\x00\x01\x02\x03\x04\x05\x06\x07\x08\x09\x0a\x0b\x0c\x0d\x0e\x0f"
        "\x10\x11\x12\x13\x14\x15\x16\x17\x18\x19\x1a\x1b\x1c\x1d\x1e\x1f"
        "\n" + (GOOD_PAGE * 2)
    )
    _patch_firecrawl(monkeypatch, mixed)
    service = _scrape_service(monkeypatch, firecrawl_key="fc_real_key_value_here")

    result = await service.scrape_url("https://example.com/article")

    assert result["success"] is True
    assert result.get("salvaged") is True
    assert "retrieval" in result["content"].lower()


async def test_scrape_url_returns_a_good_firecrawl_page_unchanged(monkeypatch):
    """The counter-test: the common case must not be slowed or altered."""
    _patch_firecrawl(monkeypatch, GOOD_PAGE, title="Real Article")
    service = _scrape_service(monkeypatch, firecrawl_key="fc_real_key_value_here")

    result = await service.scrape_url("https://example.com/article")

    assert result["success"] is True
    assert result["title"] == "Real Article"
    assert result["content"] == GOOD_PAGE
    assert "salvaged" not in result


def test_html_to_text_needs_no_third_party_package():
    """The bug: `import html2text` was not a declared dependency.

    The ModuleNotFoundError was swallowed, so the entire direct-HTTP scrape
    fallback silently returned "Unable to scrape webpage content". This asserts
    the replacement converter is reachable, and that no *import statement* in
    the scrape path still reaches for a module the project does not depend on.
    """
    out = html_to_text("<html><body><p>Hello world.</p></body></html>")
    assert "Hello world." in out

    import ast
    import pathlib

    tree = ast.parse(pathlib.Path("app/services/tools/web_search.py").read_text(encoding="utf-8"))
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            imported.add(node.module.split(".")[0])
    assert "html2text" not in imported, f"the scrape path still imports {sorted(imported)}"


def test_html_to_text_keeps_structure_and_drops_invisible_content():
    markup = (
        "<html><head><title>Doc Title</title>"
        "<style>body { color: red; }</style>"
        "<script>var secret = 1;</script></head>"
        "<body><h1>Heading</h1><p>First para.</p><ul><li>Item one.</li></ul>"
        "</body></html>"
    )
    out = html_to_text(markup)
    assert "Doc Title" in out
    assert "Heading" in out
    assert "First para." in out
    assert "Item one." in out
    assert "color: red" not in out
    assert "var secret" not in out
    # Block tags must become line breaks, not one giant run-on line.
    assert len([ln for ln in out.splitlines() if ln.strip()]) >= 4


def test_html_to_text_unescapes_entities():
    out = html_to_text("<p>Tom &amp; Jerry &mdash; caf&eacute; &#39;quoted&#39;</p>")
    assert "&" in out and "&amp;" not in out
    assert "café" in out


def test_html_to_text_survives_unclosed_script():
    """An unclosed <script> at EOF would otherwise swallow the whole page."""
    out = html_to_text("<body><p>Visible text.</p><script>never closed")
    assert "Visible text." in out
    assert "never closed" not in out


def test_html_to_text_output_passes_the_shape_check():
    """The converter and the validator must agree on what readable text is."""
    body = "".join(f"<p>{GOOD_PAGE}</p>" for _ in range(3))
    text = html_to_text(body)
    assert classify_content(text).ok, f"converter output rejected: {classify_content(text)}"


# -- C3: credential validation -------------------------------------------------


class _Cfg:
    """Minimal settings stand-in. Unset attributes read as None."""

    def __init__(self, **kwargs):
        self.__dict__.update(kwargs)


def test_required_credential_missing_is_an_error():
    finding = validate_credential("DATABASE_URL", None, required=True)
    assert finding is not None
    assert finding.severity == "error"


def test_optional_credential_missing_is_not_a_problem():
    """An unset optional provider is a valid deployment, not an error."""
    assert validate_credential("TAVILY_API_KEY", None, required=False) is None


def test_placeholder_value_is_reported():
    for bad in ("fc_placeholder_abc", "your-api-key-here", "CHANGEME", "xxxx", "<token>"):
        finding = validate_credential("TAVILY_API_KEY", bad, required=False)
        assert finding is not None, f"{bad!r} should be flagged"
        assert "placeholder" in finding.problem or "xxx" in finding.problem


def test_placeholder_never_leaks_the_value():
    finding = validate_credential("OPENROUTER_API_KEY", "sk-or-v1-your-key-here", False)
    assert finding is not None
    assert "your-key-here" not in finding.problem
    assert "your-key-here" not in str(finding.as_log_kwargs())


def test_real_looking_key_passes():
    assert validate_credential("OPENROUTER_API_KEY", "sk-or-v1-" + "a" * 40, False) is None


def test_short_secret_is_a_warning():
    finding = validate_credential("OPENROUTER_API_KEY", "sk-abc", False)
    assert finding is not None and finding.severity == "warning"
    assert "short" in finding.problem


def test_urls_are_exempt_from_the_length_rule():
    """A DSN is legitimately shorter than an API key."""
    assert validate_credential("REDIS_URL", "redis://localhost:6379/0", False) is None


def test_non_string_value_is_flagged_as_a_type_error():
    finding = validate_credential("TAVILY_API_KEY", 12345, False)
    assert finding is not None
    assert "expected a string" in finding.problem


def test_weak_jwt_secret_is_reported_once_as_an_error():
    """One weak secret must yield one actionable line, not two overlapping ones."""
    report = validate_settings(_Cfg(JWT_SECRET_KEY="short"))
    jwt = [f for f in report.findings if f.variable == "JWT_SECRET_KEY"]
    assert len(jwt) == 1
    assert jwt[0].severity == "error"


def test_strong_jwt_secret_is_clean():
    report = validate_settings(_Cfg(JWT_SECRET_KEY="k" * 48))
    assert not [f for f in report.findings if f.variable == "JWT_SECRET_KEY"]


def test_empty_report_is_ok():
    report = ValidationReport(checked=5)
    assert report.ok
    assert "all usable" in report.summary()


def test_report_separates_errors_from_warnings():
    report = ValidationReport(findings=[])
    assert report.errors == [] and report.warnings == []
    assert report.ok


def test_every_inventory_name_exists_on_settings():
    """Guards a rename: a typo here would silently skip validation."""
    from backend.app.core.config import Settings

    missing = [n for n in CREDENTIAL_NAMES if n not in Settings.model_fields]
    assert not missing, f"inventory references non-existent settings: {missing}"


def test_inventory_covers_the_real_providers():
    """The inventory was read out of the source, so keep it from regressing."""
    for name in (
        "DATABASE_URL",
        "REDIS_URL",
        "JWT_SECRET_KEY",
        "ENCRYPTION_KEY",
        "OPENROUTER_API_KEY",
        "GROQ_API_KEY",
        "GEMINI_API_KEY",
        "TAVILY_API_KEY",
        "FIRECRAWL_API_KEY",
        "QDRANT_API_KEY",
        "MEM0_API_KEY",
        "E2B_API_KEY",
        "RESEND_API_KEY",
        "SMTP_PASSWORD",
        "SUPABASE_URL",
        "SUPABASE_SERVICE_ROLE_KEY",
        "GOOGLE_OAUTH_CLIENT_SECRET",
        "GITHUB_OAUTH_CLIENT_SECRET",
        "MCP_API_KEY",
        "SENTRY_DSN",
        "LANGGRAPH_CHECKPOINT_DSN",
    ):
        assert name in CREDENTIAL_NAMES, f"{name} dropped from the inventory"


def test_inventory_has_no_duplicates():
    assert len(CREDENTIAL_NAMES) == len(set(CREDENTIAL_NAMES))


def test_inventory_names_look_like_settings_attributes():
    """A lowercase or dotted entry would be silently skipped by getattr."""
    import re

    from backend.app.core.credential_check import CREDENTIAL_NAME_PATTERN

    bad = [n for n in CREDENTIAL_NAMES if not re.fullmatch(CREDENTIAL_NAME_PATTERN, n)]
    assert not bad, f"malformed credential names: {bad}"


def test_format_report_never_prints_a_secret():
    report = validate_settings(_Cfg(TAVILY_API_KEY="fc_placeholder_SECRETISH"))
    text = "\n".join(format_report(report))
    assert "SECRETISH" not in text
    assert "TAVILY_API_KEY" in text


def test_looks_like_placeholder_reports_empty():
    assert looks_like_placeholder("") == "empty"
    assert looks_like_placeholder("   ") == "empty"
    assert looks_like_placeholder("sk-real-key-value") is None


class _RecordingLogger:
    """Stands in for the structlog logger so emitted events can be asserted."""

    def __init__(self) -> None:
        self.events: list[tuple[str, str, dict]] = []

    def _record(self, level: str, event: str, kwargs: dict) -> None:
        self.events.append((level, event, kwargs))

    def info(self, event: str, **kwargs) -> None:
        self._record("info", event, kwargs)

    def warning(self, event: str, **kwargs) -> None:
        self._record("warning", event, kwargs)

    def error(self, event: str, **kwargs) -> None:
        self._record("error", event, kwargs)

    def debug(self, event: str, **kwargs) -> None:
        self._record("debug", event, kwargs)

    def names(self) -> list[str]:
        return [event for _lvl, event, _k in self.events]


async def test_credential_check_is_wired_into_startup(monkeypatch):
    """Execute the real startup block and observe the log it emits.

    A source-grep would pass even if the call's result were thrown away, which
    is precisely the failure mode here: a check that runs but reports nothing
    is indistinguishable from no check at all. So this patches the process's
    external dependencies (db, redis, qdrant, graph pool), runs the actual
    lifespan, and asserts a broken credential is surfaced to the log.
    """
    from backend.app import main as main_mod

    monkeypatch.setattr(
        main_mod.settings, "TAVILY_API_KEY", "fc_placeholder_NOTAREALKEY", raising=False
    )

    recorder = _RecordingLogger()
    monkeypatch.setattr(main_mod, "logger", recorder)

    async def _noop() -> None:
        return None

    async def _ping():
        return True

    @asynccontextmanager
    async def _fake_graph():
        yield None

    class _FakeSessionFactory:
        def __call__(self):
            return self

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

    monkeypatch.setattr(main_mod, "init_db", _noop)
    monkeypatch.setattr(main_mod, "close_db", _noop)
    monkeypatch.setattr(main_mod.redis_client, "ping", _ping)
    monkeypatch.setattr(main_mod.vector_db, "ensure_collection", _noop)
    monkeypatch.setattr(main_mod, "lifespan_graph", _fake_graph)
    monkeypatch.setattr(
        "backend.app.infrastructure.database.session.async_session_factory",
        _FakeSessionFactory(),
    )

    await _drive_lifespan(main_mod)

    assert "credential_validation_detail" in recorder.names(), (
        f"startup emitted no credential findings: {recorder.names()}"
    )
    detail = " ".join(
        str(k.get("detail", ""))
        for _lvl, event, k in recorder.events
        if event == "credential_validation_detail"
    )
    assert "TAVILY_API_KEY" in detail
    assert "NOTAREALKEY" not in detail, "the secret value must never be logged"


async def test_credential_check_does_not_stop_startup(monkeypatch):
    """A broken provider must not take every other capability down with it."""
    from backend.app import main as main_mod

    monkeypatch.setattr(
        main_mod.settings, "OPENROUTER_API_KEY", "your-key-goes-here", raising=False
    )

    recorder = _RecordingLogger()
    monkeypatch.setattr(main_mod, "logger", recorder)

    async def _noop() -> None:
        return None

    async def _ping():
        return True

    @asynccontextmanager
    async def _fake_graph():
        yield None

    class _FakeSessionFactory:
        def __call__(self):
            return self

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

    monkeypatch.setattr(main_mod, "init_db", _noop)
    monkeypatch.setattr(main_mod, "close_db", _noop)
    monkeypatch.setattr(main_mod.redis_client, "ping", _ping)
    monkeypatch.setattr(main_mod.vector_db, "ensure_collection", _noop)
    monkeypatch.setattr(main_mod, "lifespan_graph", _fake_graph)
    monkeypatch.setattr(
        "backend.app.infrastructure.database.session.async_session_factory",
        _FakeSessionFactory(),
    )

    await _drive_lifespan(main_mod)

    assert "database_tables_initialized" in recorder.names(), (
        f"startup aborted on a credential problem: {recorder.names()}"
    )
    assert "nexus_ai_shutting_down" in recorder.names()


async def _drive_lifespan(main_mod):
    """Enter and exit the real lifespan without a live app object."""
    cm = main_mod.lifespan(None)
    await cm.__aenter__()
    await cm.__aexit__(None, None, None)


# -- C4: requires_scraping contract -------------------------------------------


def test_mark_sets_the_flag_and_reason():
    result = mark_requires_scraping({"url": "https://x"}, "snippet_only")
    assert result[REQUIRES_SCRAPING] is True
    assert result["scrape_reason"] == "snippet_only"
    assert needs_scraping(result)


def test_explicit_false_is_respected():
    assert not needs_scraping(
        {"url": "https://x", "content": GOOD_PAGE, REQUIRES_SCRAPING: False}
    )


def test_short_body_without_a_url_needs_no_scrape():
    assert not needs_scraping({"snippet": "tiny"})


def test_unlabelled_url_result_is_treated_as_a_preview():
    """The safe default: a needless scrape costs less than a snippet answer."""
    assert needs_scraping({"url": "https://x", "content": "short"})


def test_long_body_does_not_need_scraping():
    assert not needs_scraping({"url": "https://x", "content": GOOD_PAGE})


def test_full_text_threshold_is_used_as_documented():
    assert needs_scraping({"url": "https://x", "content": "x" * (FULL_TEXT_CHARS - 1)})
    assert not needs_scraping({"url": "https://x", "content": "x" * (FULL_TEXT_CHARS + 1)})


async def test_tavily_results_declare_they_are_snippet_only(monkeypatch):
    """The regression: Tavily is called with include_raw_content=False, so
    `content` is always empty and the caller has to know the body is a
    bounded snippet rather than a page."""
    import httpx
    from backend.app.services.tools.web_search import web_search_service

    class FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {
                "results": [
                    {
                        "title": "T",
                        "url": "https://a",
                        "content": "a snippet",
                        "raw_content": "",
                    }
                ]
            }

    class FakeClient:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, url, json=None):
            return FakeResponse()

    monkeypatch.setattr(httpx, "AsyncClient", FakeClient)
    results = await web_search_service._search_tavily("query", 3)
    assert results
    assert results[0][REQUIRES_SCRAPING] is True
    assert results[0]["scrape_reason"] == "tavily_snippet_only"


async def test_duckduckgo_results_are_always_flagged(monkeypatch):
    """DuckDuckGo returns no body at all, so this is the clearest case."""
    import types

    from backend.app.services.tools.web_search import web_search_service

    class FakeDDGS:
        def __init__(self, *a, **k):
            pass

        def text(self, query, max_results=5, **kwargs):
            return [{"title": "T", "href": "https://a", "body": "a snippet"}]

    fake_ddgs = types.ModuleType("ddgs")
    fake_ddgs.DDGS = FakeDDGS
    fake_exc = types.ModuleType("ddgs.exceptions")
    fake_exc.DDGSException = type("DDGSException", (Exception,), {})
    fake_ddgs.exceptions = fake_exc
    monkeypatch.setitem(__import__("sys").modules, "ddgs", fake_ddgs)
    monkeypatch.setitem(__import__("sys").modules, "ddgs.exceptions", fake_exc)

    results = await web_search_service._search_duckduckgo("query", 3, retries=1)
    assert results and results[0][REQUIRES_SCRAPING] is True
    assert results[0]["scrape_reason"] == "duckduckgo_snippet_only"


async def test_researcher_skips_scraping_a_full_body(monkeypatch):
    """The regression: the top two results were scraped unconditionally."""
    from backend.app.agents.subagents import researcher as r

    scrapes: list[str] = []

    class FakeSearch:
        async def search(self, query, max_results=5):
            return [
                {
                    "url": "https://full",
                    "content": GOOD_PAGE,
                    REQUIRES_SCRAPING: False,
                },
                {
                    "url": "https://preview",
                    "content": "tiny",
                    REQUIRES_SCRAPING: True,
                },
            ]

        async def scrape_url(self, url):
            scrapes.append(url)
            return {"url": url, "content": GOOD_PAGE, "success": True}

    async def fake_completion(**kwargs):
        assert GOOD_PAGE in kwargs["messages"][1]["content"]
        return "SYNTHESIS"

    monkeypatch.setattr(r, "web_search_service", FakeSearch())
    monkeypatch.setattr(r.ai_client, "completion", fake_completion)
    out = await r.researcher_subagent.execute("topic", max_sources=2)
    assert out["synthesis"] == "SYNTHESIS"
    assert scrapes == ["https://preview"], "only the preview should be fetched"


async def test_researcher_falls_back_to_snippet_when_scrape_is_rejected(monkeypatch):
    """A rejected scrape must not cost the turn its evidence.

    Without this, adding the content-shape guard would have *removed*
    evidence rather than filtering bad evidence out of it.
    """
    from backend.app.agents.subagents import researcher as r

    class FakeSearch:
        async def search(self, query, max_results=5):
            return [
                {
                    "url": "https://blocked",
                    "content": "",
                    "snippet": "A usable snippet about the subject matter here.",
                    REQUIRES_SCRAPING: True,
                }
            ]

        async def scrape_url(self, url):
            return {
                "url": url,
                "content": "Unable to scrape webpage content (block_page).",
                "success": False,
                "rejection_reason": "block_page",
            }

    seen: dict = {}

    async def fake_completion(**kwargs):
        seen["prompt"] = kwargs["messages"][1]["content"]
        return "SYNTHESIS"

    monkeypatch.setattr(r, "web_search_service", FakeSearch())
    monkeypatch.setattr(r.ai_client, "completion", fake_completion)
    await r.researcher_subagent.execute("topic", max_sources=1)
    assert "usable snippet" in seen["prompt"]


async def test_researcher_skips_a_result_with_no_url(monkeypatch):
    from backend.app.agents.subagents import researcher as r

    class FakeSearch:
        async def search(self, query, max_results=5):
            return [{"title": "no url", "content": GOOD_PAGE}]

        async def scrape_url(self, url):
            raise AssertionError("must not scrape a result with no url")

    async def fake_completion(**kwargs):
        return "SYNTHESIS"

    monkeypatch.setattr(r, "web_search_service", FakeSearch())
    monkeypatch.setattr(r.ai_client, "completion", fake_completion)
    out = await r.researcher_subagent.execute("topic", max_sources=1)
    assert out["synthesis"] == "SYNTHESIS"
    assert out["sources"] == []


def test_structlog_config_still_merges_contextvars():
    """A4's dependency: the version binding is only useful if this holds."""
    processors = structlog.get_config()["processors"]
    assert any("merge_contextvars" in str(p) for p in processors)
