"""
Graph Nodes for LangGraph Orchestration.
Includes Bootstrap, Planner, Orchestrator, Subagent Dispatcher, Tool Execution (with HITL), and Synthesizer.
Long-term memories from mem0 are injected into every prompt and saved after each response.
"""
from typing import Any, Literal
from uuid import UUID

import structlog
from backend.app.agents.orchestrator.hitl import request_human_approval
from backend.app.agents.orchestrator.state import AgentState
from backend.app.agents.subagents.coder import coder_subagent
from backend.app.agents.subagents.critic import critic_subagent
from backend.app.agents.subagents.researcher import researcher_subagent
from backend.app.core.config import settings
from backend.app.infrastructure.ai.litellm_client import ai_client
from backend.app.services.memory import long_term_memory
from backend.app.services.rag.critique import (
    VERDICT_UNRELATED,
    retrieval_critique_service,
)
from backend.app.services.rag_service import rag_service
from backend.app.services.structured_output import structured_service
from backend.app.services.tools.evidence_gate import evidence_gate
from backend.app.services.tools.tool_gateway import tool_gateway
from langchain_core.messages import AIMessage
from langgraph.config import get_config
from pydantic import BaseModel

logger = structlog.get_logger(__name__)

_FALLBACK_USER_ID = "00000000-0000-0000-0000-000000000000"


async def bootstrap_node(state: AgentState) -> dict[str, Any]:
    """
    Step 0: Materialises session identity (user_id, conversation_id) and
    defaulted state keys from the run config so downstream nodes never read None.
    """
    config = get_config()
    conf = (config or {}).get("configurable", {})

    return {
        "user_id": str(conf.get("user_id") or ""),
        "conversation_id": str(conf.get("conversation_id") or conf.get("thread_id") or ""),
        "trace_id": str(conf.get("trace_id") or ""),
        "mode": str(conf.get("mode") or state.get("mode") or "normal"),
        "system_prompt": state.get("system_prompt") or "You are Nexus AI — a production AI assistant.",
        "active_skills": state.get("active_skills") or [],
        "user_memories": state.get("user_memories") or [],
        "plan": state.get("plan"),
        "current_step": state.get("current_step", 0),
        "task_type": state.get("task_type", "general"),
        "pending_tool_calls": state.get("pending_tool_calls") or [],
        "tool_results": state.get("tool_results") or [],
        "hitl_approved": bool(state.get("hitl_approved")),
        "subagent_dispatches": state.get("subagent_dispatches") or [],
        "subagent_outputs": state.get("subagent_outputs") or {},
        "tot_candidates": state.get("tot_candidates") or [],
        "citations": state.get("citations") or [],
        "evidence_score": state.get("evidence_score", 0.0),
        "evidence_gate_passed": bool(state.get("evidence_gate_passed", False)),
        "revision_count": int(state.get("revision_count", 0)),
        "critique": state.get("critique"),
        "rag_relevance_score": float(state.get("rag_relevance_score", 0.0)),
        "grader_verdict": state.get("grader_verdict", "relevant"),
        "grader_confidence": float(state.get("grader_confidence", 0.0)),
        "needs_web_search": bool(state.get("needs_web_search", False)),
        "retry_count": state.get("retry_count", 0),
        "error": state.get("error"),
    }


async def planner_node(state: AgentState) -> dict[str, Any]:
    """
    Step 1: Loads user's long-term memories via mem0, then evaluates the
    user message. If complex multi-step request, builds an execution plan.
    """
    messages = state.get("messages", [])
    user_id = state.get("user_id", "")

    if not messages:
        return {"plan": None, "current_step": 0, "user_memories": []}

    last_user_msg = messages[-1].content

    # ── Load long-term memories relevant to this query (mem0 semantic search) ──
    memory_block = ""
    if user_id:
        memory_block = await long_term_memory.build_memory_context_block(
            query=last_user_msg,
            user_id=user_id,
            limit=8,
        )
        if memory_block:
            logger.info("mem0_memories_loaded_for_planner", user_id=user_id)

    # Determine if planning is needed
    plan_prompt = (
        f"Analyze this user request:\n'{last_user_msg}'\n\n"
        "If this request requires multiple actions (e.g. searching, writing code, executing data analysis), "
        "outline 2-4 brief numbered steps. If it is a direct question or simple task, respond with 'DIRECT'."
    )

    plan_response = await ai_client.completion(
        messages=[{"role": "user", "content": plan_prompt}],
        model="llama-3.1-8b-instant",
        temperature=0.1,
    )

    if "DIRECT" in plan_response.upper():
        return {"plan": None, "current_step": 0, "user_memories": [memory_block] if memory_block else []}

    steps = [line.strip() for line in plan_response.split("\n") if line.strip()]
    logger.info("planner_generated_steps", step_count=len(steps))
    return {"plan": steps, "current_step": 0, "user_memories": [memory_block] if memory_block else []}


class ActionChoice(BaseModel):
    """Structured router decision: the single primary action for a request."""
    action: Literal["RESEARCH", "CODE", "ANSWER"]


async def orchestrator_node(state: AgentState) -> dict[str, Any]:
    """
    Step 2: Core routing node. Decides next action:
    - Dispatch to Researcher Subagent
    - Dispatch to Coder Subagent
    - Run direct Tool
    - Synthesize Final Response
    """
    messages = state.get("messages", [])
    last_user_msg = messages[-1].content if messages else ""
    mode = state.get("mode", "normal")

    router_prompt = (
        f"User Request: '{last_user_msg}'\n\n"
        "Classify the primary action needed:\n"
        "- 'RESEARCH': Requires live web search or scraping\n"
        "- 'CODE': Requires writing, executing, or visualizing code in Python\n"
        "- 'ANSWER': Can be answered directly with existing knowledge or conversation context\n\n"
        "Respond ONLY with one word: RESEARCH, CODE, or ANSWER."
    )
    if mode == "code":
        router_prompt += (
            "\nNOTE: Developer mode is active — bias toward 'CODE' whenever the request "
            "involves implementation, data work, or execution."
        )
    elif mode == "research":
        router_prompt += (
            "\nNOTE: Research mode is active — bias toward 'RESEARCH' whenever the request "
            "needs current or external information."
        )

    # Structured output path (Instructor) with a raw-string fallback so a
    # parse failure never breaks routing — behavior is identical to before.
    try:
        decision = await structured_service.generate_structured(
            response_model=ActionChoice,
            messages=[{"role": "user", "content": router_prompt}],
            model="llama-3.1-8b-instant",
            temperature=0.0,
        )
        action = decision.action
        logger.info("orchestrator_structured_route", action=action)
    except Exception as exc:
        logger.warning("orchestrator_structured_route_failed_using_raw", error=str(exc))
        action = await ai_client.completion(
            messages=[{"role": "user", "content": router_prompt}],
            model="llama-3.1-8b-instant",
            temperature=0.0,
        )
        action = action.strip().upper()

    if "RESEARCH" in action:
        return {"subagent_dispatches": ["researcher"], "task_type": "research"}
    elif "CODE" in action:
        return {"subagent_dispatches": ["coder"], "task_type": "code"}
    else:
        return {"subagent_dispatches": [], "task_type": "general"}


async def subagent_dispatcher_node(state: AgentState) -> dict[str, Any]:
    """
    Step 3: Executes designated subagent (Researcher or Coder).
    In code mode, the coder's output is queued as a pending tool call so the
    sandboxed executor (and HITL approval, when required) can run the code.
    """
    dispatches = state.get("subagent_dispatches", [])
    messages = state.get("messages", [])
    mode = state.get("mode", "normal")
    user_query = messages[-1].content if messages else ""
    outputs = state.get("subagent_outputs", {})
    pending = list(state.get("pending_tool_calls") or [])

    if "researcher" in dispatches:
        res = await researcher_subagent.execute(topic=user_query)
        outputs["researcher"] = res
    elif "coder" in dispatches:
        res = await coder_subagent.execute(task_description=user_query)
        outputs["coder"] = res
        if mode == "code":
            code = res.get("code") or res.get("stdout") or res.get("synthesis")
            if code:
                pending.append({
                    "name": "execute_python",
                    "arguments": {"code": code[:20000]},
                })

    return {"subagent_outputs": outputs, "pending_tool_calls": pending}


async def tool_node(state: AgentState) -> dict[str, Any]:
    """
    Step 4: Executes any pending tool calls with HITL verification when required.
    Accepts both the legacy resume shape ({approved, reason}) and the API shape
    ({action: approve|reject|modify, data}) so either HITL resume contract works.
    """
    pending = state.get("pending_tool_calls", [])
    user_id_str = state.get("user_id", "00000000-0000-0000-0000-000000000000")
    user_id = UUID(user_id_str)
    tool_results: list[dict[str, Any]] = list(state.get("tool_results", []))

    for call in pending:
        tool_name = call.get("name")
        args = call.get("arguments", {})

        # Check if requires approval
        is_auto = tool_gateway.check_permission(tool_name)
        approved = state.get("hitl_approved", False)

        if not is_auto and not approved:
            # Trigger LangGraph interrupt for human-in-the-loop approval
            decision_raw = request_human_approval(tool_name=tool_name, arguments=args)
            decision: dict[str, Any] = decision_raw or {}
            approved = (
                decision.get("approved")
                if decision.get("approved") is not None
                else decision.get("action") == "approve"
            )
            reason = decision.get("reason") or (decision.get("data") or {}).get("reason", "")
            if not approved:
                tool_results.append({
                    "tool_name": tool_name,
                    "status": "denied",
                    "error": f"Tool execution rejected by user: {reason}",
                })
                continue

        result = await tool_gateway.execute_tool(
            tool_name=tool_name,
            arguments=args,
            user_id=user_id,
            is_user_approved=approved,
        )
        tool_results.append(result)

    return {"tool_results": tool_results, "pending_tool_calls": []}


async def critic_grader_node(state: AgentState) -> dict[str, Any]:
    """
    Step 4.5 (ANSWER path): Retrieval-quality gate before synthesis.
    1. Runs local RAG retrieval for the latest user message.
    2. Grades the retrieved context: relevant | insufficient | unrelated.
    3. On insufficient/unrelated, queues a web_search tool call so CRAG can
       supplement the answer (tool_node → synthesizer). Otherwise flows to
       synthesizer with local citations only.
    """
    messages = state.get("messages", [])
    query = messages[-1].content if messages else ""
    if not query:
        return {"pending_tool_calls": [], "needs_web_search": False}

    user_id_str = state.get("user_id") or _FALLBACK_USER_ID
    try:
        user_id = UUID(user_id_str)
    except ValueError:
        user_id = UUID(_FALLBACK_USER_ID)

    # 1. Local RAG retrieval (multi-query + conditional HyDE + child→parent)
    try:
        result = await rag_service.query(
            query=query,
            user_id=user_id,
            top_k=5,
            score_threshold=0.35,
        )
        citations = [c.model_dump() for c in result.citations]
    except Exception as exc:
        # Retrieval infrastructure unavailable → treat as no grounding and
        # fall through to CRAG web search instead of failing the whole turn.
        logger.warning("critic_retrieval_failed_falling_back_to_web", error=str(exc))
        result = None
        citations = []

    # 2. Grade retrieved context
    if result is None:
        verdict, score = VERDICT_UNRELATED, 0.0
    else:
        verdict, score = retrieval_critique_service.grade(result.citations)
    needs_web_search = verdict in ("insufficient", "unrelated")

    update: dict[str, Any] = {
        "citations": citations,
        "grader_verdict": verdict,
        "rag_relevance_score": round(score, 4),
        "grader_confidence": round(max(0.0, min(1.0, score)), 4),
        "needs_web_search": needs_web_search,
    }

    # 3. CRAG routing: queue web search for the tool node on weak grounding
    if needs_web_search:
        update["pending_tool_calls"] = [
            {"name": "web_search", "arguments": {"query": query, "max_results": 5}}
        ]
    else:
        update["pending_tool_calls"] = []

    logger.info(
        "critic_grader_evaluated",
        verdict=verdict,
        score=round(score, 4),
        citations=len(citations),
        needs_web_search=needs_web_search,
    )
    return update


async def synthesizer_node(state: AgentState) -> dict[str, Any]:
    """
    Step 5: Synthesizes subagent findings, tool results, and context into final response.
    After generating the response, saves new memories via mem0.
    """
    messages = list(state.get("messages", []))
    subagent_outputs = state.get("subagent_outputs", {})
    tool_results = state.get("tool_results", [])
    system_prompt = state.get("system_prompt", "You are Nexus AI.")
    user_id = state.get("user_id", "")
    user_memories: list[str] = state.get("user_memories", [])

    # Inject long-term memories into system prompt
    if user_memories:
        memory_block = "\n".join(user_memories)
        system_prompt = f"{system_prompt}\n\n{memory_block}"

    context_additions: list[str] = []

    if "researcher" in subagent_outputs:
        context_additions.append(f"### Research Findings:\n{subagent_outputs['researcher'].get('synthesis')}")
    if "coder" in subagent_outputs:
        coder_res = subagent_outputs["coder"]
        context_additions.append(
            f"### Executed Code & Output:\n```python\n{coder_res.get('code')}\n```\nStdout: {coder_res.get('stdout')}"
        )

    # Blended grounding: local RAG citations + any CRAG web-search tool results
    citations = state.get("citations", [])
    if citations:
        citation_block = "\n".join(
            f"[{i + 1}] ({c['filename']}, score={c.get('score', 0.0):.2f}) {c.get('content_snippet', '')[:600]}"
            for i, c in enumerate(citations)
        )
        context_additions.append(f"### Retrieved Local Context (RAG):\n{citation_block}")

    tool_results = state.get("tool_results", [])
    web_blocks: list[str] = []
    for r in tool_results:
        results = r.get("result") or []
        if isinstance(results, dict):
            results = [results]
        if not isinstance(results, list):
            continue
        for item in results:
            if not isinstance(item, dict):
                continue
            title = item.get("title") or item.get("url") or "Untitled"
            snippet = (item.get("snippet") or item.get("content") or "")[:500]
            url = item.get("url") or ""
            web_blocks.append(f"- **{title}**: {snippet}\n  {url}")
    if web_blocks:
        context_additions.append("### Web Search Results (CRAG supplement):\n" + "\n".join(web_blocks))

    if state.get("grader_verdict") in ("insufficient", "unrelated"):
        context_additions.append(
            f"### Grading Note: Local RAG context was graded '{state.get('grader_verdict')}' "
            f"(score {state.get('rag_relevance_score', 0.0)}). Prefer the web results where local "
            "evidence is missing, and clearly cite only what you used."
        )

    extra_context = "\n\n".join(context_additions)
    final_messages = [{"role": "system", "content": system_prompt}]

    for m in messages[:-1]:
        final_messages.append({"role": m.type, "content": m.content})

    last_user_content = messages[-1].content if messages else ""
    if extra_context:
        augmented_prompt = f"{last_user_content}\n\n[Context from execution]:\n{extra_context}"
    else:
        augmented_prompt = last_user_content

    final_messages.append({"role": "user", "content": augmented_prompt})

    # ── Critic-driven self-refinement loop (bounded) ───────────────────────────
    # Generate a draft, have the Critic subagent audit it, and revise with the
    # feedback applied. Accepts the draft once approved or the budget is spent.
    max_revisions = int(getattr(settings, "CRITIC_MAX_REVISIONS", 2))
    revision_count = int(state.get("revision_count", 0))
    critique: dict[str, Any] | None = None
    draft_prompt = augmented_prompt
    revisions_used = 0

    for attempt in range(max_revisions + 1):
        draft = await ai_client.completion(
            messages=final_messages[:-1] + [{"role": "user", "content": draft_prompt}],
            model="llama-3.3-70b-versatile",
            temperature=0.7,
        )
        critique = await critic_subagent.evaluate(
            user_request=last_user_content, candidate_response=draft
        )
        if critique.get("approved"):
            response_text = draft
            break
        logger.info(
            "critic_revision_requested",
            attempt=attempt + 1,
            revision_budget=max_revisions,
            feedback=str(critique.get("critique", ""))[:200],
        )
        if attempt < max_revisions:
            feedback = str(critique.get("critique", ""))[:2000]
            draft_prompt = (
                f"{draft_prompt}\n\n"
                f"[Critic Feedback — revise the draft, addressing every point raised]:\n{feedback}"
            )
            revisions_used += 1
            continue
        response_text = draft
        break

    revision_count = revision_count + revisions_used

    # ── Guardrail on the final draft (input guardrail parity on output) ─────────
    from backend.app.services.evaluation.guardrail_service import (
        guardrail_service as gs,
    )

    try:
        sanitized_draft, _pii = gs.redact_pii(response_text)
        response_text = sanitized_draft
    except Exception as exc:
        logger.warning("output_guardrail_skipped", error=str(exc))

    # ── Grounding verification via the Evidence Gate against used sources ───────
    evidence_gate_result = {"passed_gate": False, "confidence_score": 0.0, "citations": [], "reason": ""}
    raw_citations = state.get("citations", [])
    evidence_contexts: list[str] = []
    if raw_citations:
        from backend.app.domain.file.schemas import RAGCitation

        try:
            citations_models = [
                RAGCitation(**c) if not isinstance(c, RAGCitation) else c for c in raw_citations
            ]
            evidence_contexts.extend(c.content_snippet for c in citations_models)
        except Exception as exc:
            logger.warning("grounding_verification_skipped", error=str(exc))

    # Web results used by CRAG count toward grounding evidence as well.
    evidence_contexts.extend(web_blocks)

    try:
        evidence_gate_result = evidence_gate.verify_evidence_support(response_text, evidence_contexts)
        logger.info(
            "evidence_gate_evaluated_on_synthesis",
            confidence=evidence_gate_result["confidence_score"],
            passed=evidence_gate_result["passed_gate"],
            revision_count=revision_count,
        )
    except Exception as exc:
        logger.warning("evidence_gate_evaluation_failed", error=str(exc))

    # ── Save new memories from this exchange via mem0 (background, non-blocking) ──
    if user_id:
        exchange = [
            {"role": "user", "content": last_user_content},
            {"role": "assistant", "content": response_text},
        ]
        try:
            await long_term_memory.add_from_conversation(
                messages=exchange,
                user_id=user_id,
                metadata={"conversation_id": state.get("conversation_id", "")},
            )
        except Exception as exc:
            # Memory saving is best-effort — never fail the main response
            logger.warning("mem0_save_failed_non_blocking", error=str(exc))

    ai_message = AIMessage(content=response_text)
    return {
        "messages": [ai_message],
        "evidence_score": evidence_gate_result["confidence_score"],
        "evidence_gate_passed": evidence_gate_result["passed_gate"],
        "revision_count": revision_count,
        "critique": critique,
    }
