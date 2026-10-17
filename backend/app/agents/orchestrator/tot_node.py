"""
Tree of Thoughts (ToT) Exploration Node.
Implements Pattern 28: Tree of Thoughts.
Explores multiple candidate reasoning paths using breadth-first search and heuristic evaluation.

The node deliberately does **not** append its synthesized "best approach" to
``messages``. It used to return ``{"messages": [AIMessage(...)]}``, which made
the ToT synthesis the last message in the state; every downstream reader of
``messages[-1]`` -- the orchestrator's router, the critic grader's retrieval
query, the subagent dispatcher and the synthesizer's prompt -- then operated on
the ToT output instead of the user's turn. The result is carried in state
(``tot_synthesis`` / ``tot_candidates``) and offered to the synthesizer as
guidance, so the user's request stays the query.
"""
from typing import Any

import structlog
from backend.app.agents.orchestrator.state import AgentState
from backend.app.core.config import settings
from backend.app.infrastructure.ai.litellm_client import ai_client

logger = structlog.get_logger(__name__)


async def tree_of_thoughts_node(state: AgentState) -> dict[str, Any]:
    """
    Generates k alternative thought paths, evaluates each candidate path,
    and selects the highest scoring branch for complex analytical tasks.
    """
    messages = state.get("messages", [])
    if not messages:
        return {}

    user_query = messages[-1].content
    k = 3  # Number of candidate branches

    # 1. Thought Generation
    generate_prompt = [
        {
            "role": "system",
            "content": f"You are an analytical strategist. For the user's request below, generate {k} distinct, viable approaches. Make each approach concrete and self-contained: include its key reasoning and trade-offs. Separate each approach with a line containing only '---APPROACH---'.",
        },
        {"role": "user", "content": user_query},
    ]

    try:
        raw_candidates = await ai_client.completion(
            messages=generate_prompt,
            model=settings.FAST_MODEL,
            temperature=0.8,
            max_tokens=700,
        )
        approaches = [app.strip() for app in raw_candidates.split("---APPROACH---") if app.strip()]

        if not approaches:
            approaches = [raw_candidates]

        # 2. Candidate Evaluation & Scoring
        eval_prompt = [
            {
                "role": "system",
                "content": "Evaluate each candidate approach for logical soundness, completeness, and feasibility under realistic constraints. Then synthesize the strongest elements of all candidates into one optimal final response, and output only that response.",
            },
            {
                "role": "user",
                "content": f"User Problem:\n{user_query}\n\nCandidate Approaches:\n"
                + "\n\n".join(f"Candidate {i+1}:\n{app}" for i, app in enumerate(approaches)),
            },
        ]

        synthesized_best = await ai_client.completion(
            messages=eval_prompt,
            model=settings.FAST_MODEL,
            temperature=0.4,
            max_tokens=700,
        )

        logger.info("tot_exploration_complete", candidate_count=len(approaches))
        return {
            "tot_candidates": approaches,
            "tot_synthesis": synthesized_best,
        }

    except Exception as exc:
        logger.error("tot_node_failed", error=str(exc))
        return {}
