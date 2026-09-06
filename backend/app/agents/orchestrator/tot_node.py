"""
Tree of Thoughts (ToT) Exploration Node.
Implements Pattern 28: Tree of Thoughts.
Explores multiple candidate reasoning paths using breadth-first search and heuristic evaluation.
"""
from typing import Any

import structlog
from backend.app.agents.orchestrator.state import AgentState
from backend.app.infrastructure.ai.litellm_client import ai_client
from langchain_core.messages import AIMessage

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
            "content": f"You are an analytical strategist. Generate {k} distinct, creative, and viable approaches/solutions to this user request. Separate each approach clearly with '---APPROACH---'.",
        },
        {"role": "user", "content": user_query},
    ]

    try:
        raw_candidates = await ai_client.completion(
            messages=generate_prompt,
            model="llama-3.3-70b-versatile",
            temperature=0.8,
        )
        approaches = [app.strip() for app in raw_candidates.split("---APPROACH---") if app.strip()]

        if not approaches:
            approaches = [raw_candidates]

        # 2. Candidate Evaluation & Scoring
        eval_prompt = [
            {
                "role": "system",
                "content": "Evaluate each approach for logical soundess, completeness, and feasibility. Synthesize the best elements of all approaches into one optimal final response.",
            },
            {
                "role": "user",
                "content": f"User Problem:\n{user_query}\n\nCandidate Approaches:\n"
                + "\n\n".join(f"Candidate {i+1}:\n{app}" for i, app in enumerate(approaches)),
            },
        ]

        synthesized_best = await ai_client.completion(
            messages=eval_prompt,
            model="llama-3.3-70b-versatile",
            temperature=0.4,
        )

        logger.info("tot_exploration_complete", candidate_count=len(approaches))
        return {
            "messages": [AIMessage(content=synthesized_best)],
            "tot_candidates": approaches,
        }

    except Exception as exc:
        logger.error("tot_node_failed", error=str(exc))
        return {}
