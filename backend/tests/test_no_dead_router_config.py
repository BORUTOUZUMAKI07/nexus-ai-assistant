"""The LiteLLM router must have exactly one source of truth, and it is Python.

`backend/config/model_routing.yaml` sat in `backend/config/` for the life of the
repository describing a fallback strategy, three fallback tiers and router
settings. Nothing read it. `Router(...)` in
`app/infrastructure/ai/litellm_client.py` is built from Python literals, so the
file was a second, silent copy of the routing policy that had already drifted:

    yaml `fast_chat`          groq/llama-3.1-8b-instant
    code `fast_chat`          groq/qwen/qwen3.8-27b

    yaml `large_context`      groq/mixtral-8x7b-32768, 32768 ctx
    code `large_context`      shares groq/qwen/qwen3.8-27b with fast_chat

    yaml `vision_analysis`    groq/llama-3.2-11b-vision-preview
    code `vision_analysis`    a gemma model

    yaml cooldown             60s, max_retries_per_model 2
    code cooldown_time        30, num_retries 1

    yaml `audio_transcription` groq/whisper-large-v3
    code                     no such group at all

Nothing caught it because a dead YAML file is not an error: it parses fine, it
is plausible, and a reader editing it would reasonably believe they were
changing routing. That is worse than having no file. It is deleted, and this
module asserts the property that made it dangerous cannot come back unnoticed.

The assertion is deliberately narrow. It does not claim "every file in
backend/config/ is read" -- that is false for `task_contracts.yaml` and
`eval_criteria.yaml`, which are documentation-adjacent, and a test encoding a
half-true claim is worse than no test. It claims the specific thing: no file
anywhere in the tree may declare LiteLLM `model_list` or `fallbacks` policy
for the app's router, because the router's policy is code.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
REPO = BACKEND.parent

DELETED = BACKEND / "config" / "model_routing.yaml"


def test_the_dead_routing_yaml_is_gone() -> None:
    assert not DELETED.exists(), (
        f"{DELETED.name} is back. Nothing reads it — Router() is built from "
        "Python literals — and it disagrees with the code about fast_chat, "
        "large_context, vision_analysis, the cooldown and the retry count."
    )


def _tracked_yaml_files() -> list[Path]:
    """YAML files git knows about, falling back to a filesystem walk.

    Enumerating the *repository* rather than the working tree matters here. This
    checkout contains `.kilo/worktrees/debonair-redcurrant/`, an untracked
    agent-tooling worktree holding a full copy of the project including the file
    being deleted. A `rglob` scan fails on that copy, which says nothing about
    whether this repository is clean.

    The fallback keeps the test runnable where git is unavailable (an exported
    tarball, a vendored copy), and errs toward scanning more rather than less.
    """
    try:
        out = subprocess.run(
            ["git", "ls-files", "*.yml", "*.yaml"],
            cwd=REPO,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=60,
            check=True,
        ).stdout
        return [REPO / line for line in out.splitlines() if line.strip()]
    except (OSError, subprocess.SubprocessError):
        skip = {".git", ".venv", "node_modules", ".next", ".kilo"}
        return [
            p
            for p in sorted(REPO.rglob("*.y*ml"))
            if not (skip & set(p.parts))
        ]


def test_no_yaml_declares_router_policy() -> None:
    """A second source of truth for routing is the failure, not the file.

    Searched across the whole repo rather than just `backend/config/`, because a
    copy placed elsewhere is the same problem wearing a different hat. The
    matcher keys on the YAML keys LiteLLM itself understands, so an unrelated
    file that happens to mention the word "models" is not flagged.
    """
    offenders: list[str] = []
    for yaml_path in _tracked_yaml_files():
        if not yaml_path.exists():
            continue
        try:
            text = yaml_path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        # `model_list` is the key LiteLLM's own config schema uses; the others
        # are the nesting this file used. Any of them in a YAML file means the
        # router's policy is being declared outside Python.
        for marker in ("model_list:", "routing_strategy:", "fallback_tiers:"):
            if marker in text:
                offenders.append(
                    f"{yaml_path.relative_to(REPO)} declares {marker.rstrip(':')}"
                )
                break

    assert not offenders, (
        "LiteLLM routing policy declared outside Python; Router() is built from "
        f"literals in litellm_client.py, so these are dead files that will "
        f"drift: {offenders}"
    )


def test_router_is_built_without_a_config_path() -> None:
    """Belt and braces on the Python side.

    If `Router(...)` ever grows a `config_path=` pointing at a YAML file, the
    file's contents start winning over the literals, silently, and the contract
    test that pins `model_list` starts describing a file it does not read.
    """
    source = (BACKEND / "app" / "infrastructure" / "ai" / "litellm_client.py").read_text(
        encoding="utf-8"
    )
    assert "config_path" not in source, (
        "litellm_client.py passes config_path to Router(); the yaml it names "
        "now takes precedence over the Python literals and this module's "
        "assumptions no longer hold"
    )
