"""
Content-shape validation for scraped web pages.

The defect this fixes: ``WebSearchService.scrape_url`` returned whatever the
provider handed back, and callers treated it as evidence. Three shapes make
that actively harmful rather than merely useless:

* **Block / challenge pages.** Cloudflare interstitials, "enable JavaScript",
  "verify you are human" and rate-limit notices return HTTP 200 with a short
  body. Forwarded to the model as a source, they read as *content that
  happens to say "Access Denied"* — which then either gets cited or, worse,
  gets paraphrased into a fabricated fact about the topic.

* **Unextracted PDFs and binary documents.** A PDF that the converter could
  not process yields a few hundred bytes of raw bytes mojibake. Low token
  count, so it slips past every length check, and it is the *shape* that is
  wrong, not the length.

* **Navigation shells.** Cookie banners, "enable JavaScript to continue" and
  sitemap-only pages have real text volume and no content.

All of these are distinguished by *shape*, which is why a length check alone
cannot catch them: the malicious-looking ones are short.

Deliberately no new dependency. Every signal here is a string heuristic over
text we already hold, so this costs microseconds and cannot itself fail the
hot path.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

#: Phrases that mean "you did not get the page", not "the page says this".
#: Matched against the visible text. Each entry is a real interstitial seen in
#: the wild; the r-prefixed ones are normalised to survive markup between words.
_BLOCK_MARKERS: tuple[str, ...] = (
    "access denied",
    "access to this page has been denied",
    "you don't have permission to access",
    "403 forbidden",
    "404 not found",
    "page not found",
    "are you a robot",
    "verify you are human",
    "are you a human",
    "checking your browser",
    "just a moment",
    "enable javascript and cookies to continue",
    "please enable javascript",
    "javascript is disabled",
    "request unsuccessful. incapsula",
    "attention required! | cloudflare",
    "ddos protection by",
    "rate limit exceeded",
    "too many requests",
    "you have been blocked",
    "captcha",
    "cf-browser-verification",
    "unusual traffic from your computer network",
    "this site can't be reached",
    "site can't be reached",
    "connection timed out",
    "the requested url was not found on this server",
    "we've detected unusual traffic",
)

#: A page whose *entire* visible text is one of these is a shell, not content.
_SHELL_ONLY = re.compile(
    r"^\s*(?:loading\.{0,3}|please wait\.{0,3}|redirecting\.{0,3}|"
    r"enable javascript|javascript is required|"
    r"home|menu|navigation|sitemap|skip to (?:main )?content)\s*$",
    re.IGNORECASE,
)

#: Characters that are *not* mojibake: control whitespace, printable ASCII,
#: and the scripts a real web page can legitimately be written in. Written as
#: an explicit allowlist with escapes rather than literal characters, because a
#: literal range is unreadable in review and easy to corrupt in transit -- an
#: earlier version of this regex spanned to U+FFFF, which classified every CJK
#: page as binary noise.
#:
#: Anything outside this set is mojibake: C0/C1 control bytes, unpaired
#: surrogates, the private use area, and unassigned code points. Those only
#: appear when bytes were decoded as text when they were never text.
_TEXT_ALLOWED = (
    "\x09\x0a\x0d"          # tab, newline, carriage return
    "\x20-\x7e"             # printable ASCII
    "\xa0-\xff"             # Latin-1 supplement: e-acute, u-umlaut, n-tilde
    "\u0100-\u024f"         # Latin extended-A/B
    "\u0370-\u03ff"         # Greek
    "\u0400-\u04ff"         # Cyrillic
    "\u0590-\u05ff"         # Hebrew
    "\u0600-\u06ff"         # Arabic
    "\u0900-\u097f"         # Devanagari
    "\u0e00-\u0e7f"         # Thai
    "\u0300-\u036f"         # combining diacritical marks
    "\u2000-\u206f"         # general punctuation: em dash, curly quotes
    "\u20a0-\u20bf"         # currency symbols
    "\u2190-\u21ff"         # arrows
    "\u2200-\u22ff"         # mathematical operators
    "\u2460-\u24ff"         # enclosed alphanumerics
    "\u3000-\u303f"         # CJK symbols and punctuation
    "\u3040-\u30ff"         # Japanese kana
    "\u3400-\u4dbf"         # CJK extension A
    "\u4e00-\u9fff"         # CJK unified ideographs
    "\uac00-\ud7af"         # Hangul syllables
    "\ufb00-\ufdff"         # Hebrew/Arabic presentation forms
    "\ufeff"                # byte-order mark
    "\uff00-\uffef"         # halfwidth and fullwidth forms
)
_MOJIBAKE = re.compile("[^" + _TEXT_ALLOWED + "]")

#: Extracted prose has sentence terminators and reasonable word lengths.
_SENTENCE_END = re.compile(r"[.!?](?:\s|$)")

#: Minimum useful characters for a page to be worth forwarding at all.
MIN_USEFUL_CHARS = 200

#: PDF/blob signatures that a converter can silently fail to process.
_PDF_SIGNATURE = "%PDF-"

#: Block-level tags, which become line breaks. A scraper that did not turn
#: these into newlines produces one enormous line, which then reads to the
#: model as a single sentence and defeats any sentence-level heuristics.
_BLOCK_TAGS = (
    "p div br hr h1 h2 h3 h4 h5 h6 li tr td th blockquote pre section "
    "article header footer nav aside table ul ol dl dt dd figure figcaption"
).split()

#: Tags whose *content* is never visible text.
_DROP_WITH_CONTENT = ("script", "style", "noscript", "template", "svg", "canvas", "iframe")


def html_to_text(markup: str) -> str:
    """Convert an HTML document to readable plain text, using only the stdlib.

    Why this exists: the direct-HTTP scrape fallback imported ``html2text``,
    which is not a declared dependency and is not installed. The ``ModuleNot-
    FoundError`` was swallowed by the surrounding ``except Exception``, so
    every scrape without Firecrawl configured silently returned "Unable to
    scrape webpage content" -- the entire fallback path was dead.

    Adding the dependency was rejected: it needs an advisory review and a lock
    update, and this path only needs *readable text* for evidence, not
    high-fidelity markdown. The primary provider path (Firecrawl) still
    supplies markdown.

    Fidelity is deliberately modest and that is fine here: a missing heading
    marker costs the model some structure, whereas a missing dependency costs
    it the whole page.
    """
    import html as html_module

    text = markup
    # Remove invisible content first, so its contents cannot be resurrected
    # as text by the later tag strip.
    for tag in _DROP_WITH_CONTENT:
        text = re.sub(rf"(?is)<{tag}\b.*?</{tag}\s*>", " ", text)
    # Unclosed <script>/<style> at EOF would otherwise swallow the rest.
    text = re.sub(r"(?is)<(script|style)\b.*$", " ", text)

    # Preserve the title, which is the single most useful line of metadata.
    title_match = re.search(r"(?is)<title\b[^>]*>(.*?)</title\s*>", text)
    title = ""
    if title_match:
        title = _collapse(html_module.unescape(re.sub(r"(?s)<[^>]+>", " ", title_match.group(1))))

    # Block tags become newlines before the tags themselves are stripped, so
    # paragraph and list structure survives.
    for tag in _BLOCK_TAGS:
        text = re.sub(rf"(?i)</?{tag}\b[^>]*>", "\n", text)
    text = re.sub(r"(?s)<[^>]+>", " ", text)
    text = html_module.unescape(text)

    lines = [_collapse(line) for line in text.splitlines()]
    body = "\n".join(line for line in lines if line)

    if title and title not in body[:200]:
        return f"{title}\n\n{body}"
    return body


def _collapse(line: str) -> str:
    """Squeeze runs of whitespace, keeping single spaces between words."""
    return re.sub(r"[^\S\n]+", " ", line).strip()


@dataclass(frozen=True)
class ContentVerdict:
    """Shape assessment of a scraped body."""

    ok: bool
    reason: str = ""
    detail: str = ""

    def __bool__(self) -> bool:
        return self.ok


def _visible_text(markdown_or_html: str) -> str:
    """Crude tag strip so markers are matched against prose, not markup.

    A page saying "enable JavaScript" inside a <noscript> tag still means the
    scrape failed, but a class="enable-javascript-toggle" in a stylesheet link
    does not. Stripping tags first avoids the false positive without needing a
    real HTML parser.
    """
    text = re.sub(r"(?is)<(script|style|noscript)\b.*?</\1>", " ", markdown_or_html)
    text = re.sub(r"(?s)<[^>]+>", " ", text)
    return text.strip()


def classify_content(
    content: str | None,
    *,
    url: str = "",
    content_type: str = "",
    min_chars: int = MIN_USEFUL_CHARS,
) -> ContentVerdict:
    """Decide whether scraped content is real, readable page content.

    Returns a verdict carrying its reason so a rejection is explainable in
    logs and to the caller, rather than an opaque empty result.
    """
    if content is None or not str(content).strip():
        return ContentVerdict(False, "empty_content", "scraper returned nothing")

    raw = str(content)

    # Binary / unextracted documents. Checked before the prose heuristics
    # because a raw PDF fails every one of them anyway, and naming it
    # precisely is more actionable than "looks like noise".
    if _PDF_SIGNATURE in raw[:1024] or "application/pdf" in content_type.lower():
        return ContentVerdict(
            False, "unprocessed_pdf", "PDF was not converted to text"
        )
    if "\x00" in raw[:2048]:
        return ContentVerdict(False, "binary_content", "body contains null bytes")

    visible = _visible_text(raw)
    lowered = visible.lower()
    if not lowered.strip():
        return ContentVerdict(
            False, "no_visible_text", "markup only, no readable text"
        )

    # Block / challenge pages. The whole document must be short, otherwise a
    # legitimate article that happens to quote "403 Forbidden" in a body
    # section is not a block page.
    if len(visible) < 2000:
        for marker in _BLOCK_MARKERS:
            if marker in lowered:
                return ContentVerdict(
                    False, "block_page", f"interstitial marker: {marker!r}"
                )

    # Mojibake: mostly non-ASCII with no sentence structure.
    mojibake_ratio = len(_MOJIBAKE.findall(visible)) / max(1, len(visible))
    if mojibake_ratio > 0.15 and not _SENTENCE_END.search(visible):
        return ContentVerdict(
            False,
            "undecodable_text",
            f"{mojibake_ratio:.0%} non-ASCII with no sentence structure",
        )

    if _SHELL_ONLY.match(visible):
        return ContentVerdict(False, "navigation_shell", "only chrome text")

    if len(visible) < min_chars:
        return ContentVerdict(
            False, "too_short", f"{len(visible)} chars < {min_chars}"
        )

    return ContentVerdict(True)


def salvage_sentences(content: str, min_sentence_chars: int = 40) -> str:
    """Recover the readable prose from a partially-garbled scrape.

    Used instead of discarding a body outright when it is mostly usable: a
    page with a binary header but readable paragraphs still carries evidence.
    Returns "" when nothing survives, so the caller can fall back cleanly.
    """
    visible = _visible_text(content or "")
    out: list[str] = []
    for line in visible.splitlines():
        line = line.strip()
        if len(line) < min_sentence_chars:
            continue
        if _MOJIBAKE.search(line):
            # Only drop the line when it is mostly garbage, not merely
            # containing one curly quote or an em dash.
            if len(_MOJIBAKE.findall(line)) / max(1, len(line)) > 0.15:
                continue
        out.append(line)
    return "\n".join(out)


def scrape_result_verdict(result: dict[str, Any]) -> ContentVerdict:
    """Apply :func:`classify_content` to a ``scrape_url`` result dict."""
    if not isinstance(result, dict):
        return ContentVerdict(False, "malformed_result", f"got {type(result).__name__}")
    if result.get("success") is False:
        detail = str(result.get("content") or "")[:120]
        return ContentVerdict(False, "scrape_reported_failure", detail)
    return classify_content(
        result.get("content"),
        url=str(result.get("url") or ""),
        content_type=str(result.get("content_type") or ""),
    )
