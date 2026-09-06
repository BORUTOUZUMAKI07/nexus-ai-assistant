"""
Unit tests for Prompt Compiler and Attention Budget Context Compactor.
"""
from backend.app.domain.prompt.models import Skill
from backend.app.services.context_compiler import context_compiler
from backend.app.services.prompt_compiler import prompt_compiler


def test_prompt_compiler_ordering():
    skills = [
        Skill(
            name="DataAnalyst",
            description="Analyzes data",
            category="analytics",
            instructions="Use pandas and matplotlib",
        )
    ]
    compiled = prompt_compiler.compile_system_prompt(
        custom_instructions="Be extremely concise.",
        active_skills=skills,
    )

    # Static core directive must be at the very top for KV cache hit optimization
    assert compiled.startswith("# Core Directive")
    assert "# Custom Instructions" in compiled
    assert "DataAnalyst" in compiled


def test_context_compiler_token_estimation():
    tokens = context_compiler.estimate_tokens("Hello, how are you doing today?")
    assert tokens > 0
    assert tokens < 20
