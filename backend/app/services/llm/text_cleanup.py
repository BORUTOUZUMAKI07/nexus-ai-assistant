"""Strip model *protocol* markup out of LLM text output.

Why this exists
---------------
Some models answer a chat request with a **tool call written as text** rather
than returning it on the structured ``tool_calls`` field. This is not
hypothetical: ``liquid/lfm-2.5-2.6b:free`` (the ``liquid_fallback`` group, and
the last stop in every prose fallback chain) answered a plain question in the
agentic synthesizer with

    <|tool_call_start|>[google(query='why does the sky appear blue'), ...]<|tool_call_end|>

and that string was delivered to the user verbatim as the assistant's answer.
The provider-level fallback chain had picked the weakest model because the
primary Groq groups were returning empty, so the failure is exactly the kind
that shows up when everything else is already degraded -- and the user sees
markup where an answer should be.

The fix belongs at the one boundary every text consumer crosses: the content
extraction in ``LiteLLMService.complete``. Cleaning there means the synthesizer,
the planner, the router, the critic and every subagent all get clean text, and a
response that was *nothing but* a tool call becomes empty and takes the same
retry/failover path as a genuinely empty one instead of masquerading as an
answer.

What is removed
---------------
* Whole tool-call spans, closed or truncated-at-end: ``<|tool_call_start|>`` /
  ``<|tool_call_end|>``, the ``tool_calls_section_*`` pair, ``<tool_call>`` /
  ``</tool_call>`` and ``[tool_call]`` / ``[/tool_call]``.
* Residual model control tokens of the form ``<|anything|>`` (``<|im_start|>``,
  ``<|eot_id|>``, ``<|python_tag|>``, ...).

What is deliberately *not* removed: ordinary prose and code. A comparison like
``a < b``, an HTML/XML tag, or a generic ``List<int>`` has no ``<|...|>`` pair
and no tool-call wrapper, so it survives. This is a protocol scrubber, not a
general HTML stripper.
"""
import re

# Closed spans: drop everything between the markers, including the arguments
# the model nested inside them (Liquid writes `[fn(args)]` between its markers).
_TOOL_SPAN_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"<\|tool_call_start\|>.*?<\|tool_call_end\|>", re.DOTALL),
    re.compile(
        r"<\|tool_calls_section_start\|>.*?<\|tool_calls_section_end\|>",
        re.DOTALL,
    ),
    re.compile(r"<tool_call>.*?</tool_call>", re.DOTALL),
    re.compile(r"\[tool_call\].*?\[/tool_call\]", re.DOTALL),
)

# A start marker with no end marker means the model was truncated mid-tool-call.
# Everything from the marker on is protocol, so drop it to the end rather than
# leaving the bare arguments visible.
_UNCLOSED_TOOL_SPAN = re.compile(
    r"<\|(?:tool_call_start|tool_calls_section_start)\|>.*\Z",
    re.DOTALL,
)

# Any remaining control token. Bounded so a pathological run of pipes cannot make
# this quadratic, and `[^|>]` keeps it from matching across two adjacent tokens.
_SPECIAL_TOKEN = re.compile(r"<\|[^|>\n]{1,64}\|>")


def strip_model_artifacts(text: str) -> str:
    """Return ``text`` with tool-call markup and control tokens removed.

    Pure and total: never raises, and returns the input unchanged when there is
    nothing to remove. Whitespace left behind by a removal is collapsed so a
    stripped span does not leave a run of blank lines in the answer.
    """
    if not text:
        return text

    cleaned = text
    for pattern in _TOOL_SPAN_PATTERNS:
        cleaned = pattern.sub("", cleaned)
    cleaned = _UNCLOSED_TOOL_SPAN.sub("", cleaned)
    cleaned = _SPECIAL_TOKEN.sub("", cleaned)

    # Tidy the gaps a removal leaves without touching meaningful formatting.
    cleaned = re.sub(r"[ \t]+\n", "\n", cleaned)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    return cleaned.strip()
