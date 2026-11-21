"""The prompt compiler owns the memory-block append, not each call site.

Long-term memory recall returns an already formatted block. Before this seam,
``/messages`` and the orchestrator synthesizer each hand-built
``f"{prompt}\\n\\n{block}"``, so the same prompt text had three definitions. The
compiler method must preserve that exact text and be the one place callers use.
"""
from backend.app.services.prompts.compiler import PromptCompiler


def test_append_memory_block_preserves_the_existing_prompt_text():
    compiler = PromptCompiler(base_prompt="UNUSED")

    assert compiler.append_memory_block("SYSTEM", "- User prefers dark mode") == (
        "SYSTEM\n\n- User prefers dark mode"
    )


def test_append_memory_block_leaves_the_prompt_untouched_without_a_block():
    compiler = PromptCompiler(base_prompt="UNUSED")

    assert compiler.append_memory_block("SYSTEM", "") == "SYSTEM"
    assert compiler.append_memory_block("SYSTEM", None) == "SYSTEM"
