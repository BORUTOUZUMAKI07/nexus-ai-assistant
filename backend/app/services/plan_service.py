"""
Plan Application Service — the plan-then-approve use case.

``prepare_plan`` drafts a plan for a task using a direct LLM planner call
(bounded: no tools run while drafting — the fail-safe "no mutations before
approval" contract lives here). ``approve``/``reject`` transition a pending
plan; only approved plans are surfaced to the agent path as execution guidance.
"""
import json
import re
from typing import Awaitable, Callable
from uuid import UUID

import structlog
from backend.app.core.exceptions import ApprovalConsumedError, ResourceNotFoundError
from backend.app.domain.conversation.repository import ConversationRepository
from backend.app.domain.plan.models import Plan
from backend.app.domain.plan.repository import PlanRepository
from backend.app.infrastructure.ai.litellm_client import ai_client
from sqlmodel.ext.asyncio.session import AsyncSession

logger = structlog.get_logger(__name__)

# Plan is a JSON contract the planner must return: title, optional summary and
# 1-8 ordered, concrete steps. Asking for strict JSON keeps the fallback path
# small and the approval UI predictable.
_PLANNER_SYSTEM_PROMPT = """You are a senior technical planner. Draft a concise
execution plan for the user's task. Respond with STRICT JSON only, no prose:

{"title": string (max 8 words), "summary": string | null (one sentence of context), "steps": [string, ...]}

Rules:
- 1 to 8 steps, each an actionable imperative ("Search X", "Write Y", "Verify Z").
- Never draft steps that mutate or delete data without saying so explicitly.
- No markdown, no code fences, no commentary around the JSON."""


def _strip_code_fence(raw: str) -> str:
    text = raw.strip()
    match = re.search(r"```(?:json)?\s*([\s\S]*?)```", text)
    if match:
        return match.group(1).strip()
    return text


def _parse_plan_json(raw: str) -> tuple[str, str | None, list[str]] | None:
    """Best-effort ``(title, summary, steps)`` extraction from LLM output."""
    try:
        payload = json.loads(_strip_code_fence(raw))
    except (json.JSONDecodeError, ValueError):
        return None
    if not isinstance(payload, dict):
        return None
    title = payload.get("title")
    steps = payload.get("steps")
    if not isinstance(title, str) or not title.strip():
        return None
    if not isinstance(steps, list) or not steps or len(steps) > 8:
        return None
    cleaned_steps: list[str] = []
    for step in steps:
        if isinstance(step, str) and step.strip():
            cleaned_steps.append(step.strip())
    if not cleaned_steps:
        return None
    summary = payload.get("summary")
    return (
        title.strip()[:80],
        summary.strip()[:2000] if isinstance(summary, str) and summary.strip() else None,
        cleaned_steps[:8],
    )


def _heuristic_plan(task: str) -> tuple[str, str | None, list[str]]:
    """Offline fail-open plan: sentence-split the task into concrete steps."""
    title = task.strip().splitlines()[0][:60] or "Plan"
    sentences = [s.strip() for s in re.split(r"[.!?]\s+", task.strip()) if s.strip()]
    if len(sentences) >= 2:
        steps = sentences[:6]
    elif len(task.strip()) > 160:
        # Single run-on sentence: chunk by punctuation-free length thirds.
        body = task.strip()
        third = max(1, len(body) // 3)
        steps = [body[i : i + third].strip() for i in range(0, len(body), third)][:3]
    else:
        steps = [task.strip()]
    return title, "Heuristic plan generated offline (LLM planner unavailable).", steps


Planner = Callable[[str, list], Awaitable[tuple[str, str | None, list[str]]]]


async def _default_planner(task: str, history: list) -> tuple[str, str | None, list[str]]:
    """LLM planner with schema-enforced JSON output + retry-with-repair.

    Industry standard (Opik/structured-output guides): request a provider-level
    ``json_object`` schema first, and when the model still returns unparseable
    text, send a single repair pass feeding the validation error back before
    falling through to the offline heuristic. Never raises.
    """
    messages: list[dict[str, str]] = [
        {"role": "system", "content": _PLANNER_SYSTEM_PROMPT}
    ]
    for msg in history[-6:]:
        role = "assistant" if getattr(msg, "role", "") == "assistant" else "user"
        messages.append({"role": role, "content": getattr(msg, "content", "") or ""})
    messages.append({"role": "user", "content": task})

    try:
        raw = await ai_client.completion(
            messages=messages,
            model="complex_reasoning",
            temperature=0.4,
            max_tokens=1024,
            response_format={"type": "json_object"},
        )
        parsed = _parse_plan_json(raw)
        if parsed:
            return parsed
        # ── Retry-with-repair (schema enforcement is not guaranteed) ───────────
        logger.info("plan_llm_output_unparseable_repairing", raw=raw[:300])
        repair_messages = list(messages) + [
            {
                "role": "user",
                "content": (
                    "Your previous response did not parse as the required JSON "
                    "schema (title: string, summary: string|null, steps: array of "
                    "strings). Return ONLY the corrected JSON object, no prose, no "
                    "code fences."
                ),
            }
        ]
        raw = await ai_client.completion(
            messages=repair_messages,
            model="complex_reasoning",
            temperature=0.2,
            max_tokens=1024,
            response_format={"type": "json_object"},
        )
        parsed = _parse_plan_json(raw)
        if parsed:
            return parsed
        logger.info("plan_llm_output_unparseable_after_repair", raw=raw[:300])
    except Exception as exc:
        logger.warning("plan_llm_failed_using_heuristic", error=str(exc), task_len=len(task))

    return _heuristic_plan(task)


class PlanService:
    """
    Application service for drafting and resolving execution plans.
    """

    def __init__(
        self,
        session: AsyncSession,
        repo: PlanRepository | None = None,
        planner: Planner = _default_planner,
    ) -> None:
        self._session = session
        self._repo = repo or PlanRepository(session)
        self._planner = planner

    async def prepare_plan(self, conversation_id: UUID, user_id: UUID, task: str) -> Plan:
        history = await ConversationRepository(self._session).get_messages(
            conversation_id, limit=8
        )
        title, summary, steps = await self._planner(task, history)
        plan = await self._repo.create_plan(
            conversation_id=conversation_id,
            user_id=user_id,
            title=title,
            summary=summary,
            steps=steps,
        )
        logger.info(
            "plan_prepared",
            plan_id=str(plan.id),
            conversation_id=str(conversation_id),
            steps=len(plan.steps),
        )
        return plan

    async def list_plans(
        self, conversation_id: UUID, user_id: UUID, limit: int = 20
    ) -> list[Plan]:
        return await self._repo.list_for_conversation(conversation_id, user_id, limit=limit)

    async def approve(self, plan_id: UUID, user_id: UUID) -> Plan:
        plan = await self._repo.get_by_id(plan_id, user_id=user_id)
        if not plan:
            raise ResourceNotFoundError("Plan", str(plan_id))
        if plan.status != "pending":
            raise ApprovalConsumedError(
                f"Plan '{plan_id}' is already resolved (status={plan.status})."
            )
        plan = await self._repo.update_status(plan, "approved")
        logger.info("plan_approved", plan_id=str(plan.id))
        return plan

    async def reject(self, plan_id: UUID, user_id: UUID, reason: str | None = None) -> Plan:
        plan = await self._repo.get_by_id(plan_id, user_id=user_id)
        if not plan:
            raise ResourceNotFoundError("Plan", str(plan_id))
        if plan.status != "pending":
            raise ApprovalConsumedError(
                f"Plan '{plan_id}' is already resolved (status={plan.status})."
            )
        plan = await self._repo.update_status(plan, "rejected", reason=reason)
        logger.info("plan_rejected", plan_id=str(plan.id))
        return plan
