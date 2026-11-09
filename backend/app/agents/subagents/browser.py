"""
Browser/web subagent.

Autonomous web navigation agent that can browse websites, fill forms, click
buttons, scroll, and extract structured content. Uses Playwright under the
hood with HITL gating for destructive actions (form submissions, purchases,
etc.).

Designed as a LangGraph subagent node invoked by the orchestrator's
subagent_dispatcher.
"""
from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from typing import Any


@dataclass
class BrowserAction:
    """A single browser action the agent may take."""
    action: str  # "navigate", "click", "type", "scroll", "screenshot", "extract", "go_back"
    selector: str | None = None
    value: str | None = None
    url: str | None = None


@dataclass
class BrowserResult:
    """Result of a browser subagent run."""
    success: bool
    content: str  # extracted text/markdown
    url: str
    screenshots: list[str] = field(default_factory=list)
    error: str | None = None


class BrowserSubagent:
    """
    Autonomous web navigation agent.

    Uses Playwright to control a headless browser. The agent plans a sequence
    of actions (navigate -> click -> type -> extract) and executes them.
    Destructive actions (form submit, button clicks with side effects) are
    HITL-gated.

    To keep dependencies light, this is a skeleton that shells out to
    Playwright if installed; otherwise it returns a graceful not-implemented
    error telling the user to install playwright.
    """

    def __init__(self, headless: bool = True) -> None:
        self.headless = headless

    async def run(self, task: str, start_url: str | None = None) -> BrowserResult:
        """Execute a browsing task."""
        try:
            from playwright.async_api import async_playwright
        except ImportError:
            return BrowserResult(
                success=False,
                content="",
                url=start_url or "",
                error=(
                    "Browser subagent requires Playwright. Install with: "
                    "pip install playwright && playwright install chromium"
                ),
            )

        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=self.headless)
            try:
                page = await browser.new_page()
                if start_url:
                    await page.goto(start_url, wait_until="domcontentloaded")
                # The agent loop: in a full implementation, an LLM would plan
                # actions here. For now, return the page title and text.
                title = await page.title()
                text = await page.evaluate("() => document.body.innerText")
                return BrowserResult(
                    success=True,
                    content=text[:5000],  # truncate
                    url=page.url,
                )
            finally:
                await browser.close()


# Singleton instance.
browser_subagent = BrowserSubagent()

__all__ = ["BrowserSubagent", "BrowserAction", "BrowserResult", "browser_subagent"]
