"""
Two-phase tool execution for MwanaBot.

Phase 1: ask the LLM (with tools bound) which tools should be called for
this user message. Gemini returns either a normal answer (no tool calls
needed) OR a list of `tool_calls`.

Phase 2: if there are tool calls, execute them concurrently and return
the observations as a single human-readable string. The caller (the
streaming endpoint) then injects that string into the final prompt and
streams the answer normally.

Why two phases instead of a full ReAct loop:
- We don't need multi-step reasoning across many tools — every parent
  question maps cleanly to 0 or 1 tool call.
- Streaming + multi-step tool loops is fiddly with Gemini today; the
  two-phase pattern keeps the streaming path clean.
- Failures in tool execution become visible context for the LLM rather
  than aborting the whole turn.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_core.tools import StructuredTool
from langchain_google_genai import ChatGoogleGenerativeAI

from app.config import Settings
from app.prompts import SYSTEM_PROMPT


logger = logging.getLogger("mwanabot.tool_runner")


@dataclass
class ToolRunResult:
    observations: str
    tool_names: list[str]


# Hard limit on the number of tool calls we'll execute for one user
# question. Defends against an LLM that decides to fan-out to every
# tool ("just in case"). The LLM rarely wants more than 1-2 tools.
MAX_TOOL_CALLS = 4


_DECISION_PROMPT_TEMPLATE = (
    "Tu dois decider si tu peux repondre a la question du parent en "
    "utilisant les outils disponibles. Si oui, appelle les outils "
    "necessaires. Si la question est generique (pas besoin de donnees "
    "personnelles), reponds directement sans appeler d'outils.\n\n"
    "Question du parent : {question}"
)


async def _execute_tool(tool: StructuredTool) -> str:
    """Runs a zero-arg tool and returns its string output. Failures are
    converted to a short error string so the LLM can still produce a
    useful response."""
    try:
        result = await tool.ainvoke({})
        return str(result)
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning("Tool %s failed: %s", tool.name, exc)
        return f"Erreur lors de l'appel de l'outil {tool.name} : {exc}"


async def decide_and_run_tools(
    settings: Settings,
    *,
    question: str,
    tools: list[StructuredTool],
) -> ToolRunResult:
    """
    Phase 1: ask Gemini (with tools bound) which tools to invoke.
    Phase 2: invoke them concurrently and aggregate the observations.

    Returns ToolRunResult(observations="", tool_names=[]) when the LLM
    decides no tool is needed (or when no tools are bound).
    """

    if not tools:
        return ToolRunResult(observations="", tool_names=[])

    decider = ChatGoogleGenerativeAI(
        model=settings.gemini_model,
        google_api_key=settings.google_api_key,
        temperature=0.0,  # deterministic tool selection
    ).bind_tools(tools)

    prompt = [
        SystemMessage(content=SYSTEM_PROMPT),
        HumanMessage(content=_DECISION_PROMPT_TEMPLATE.format(question=question)),
    ]

    try:
        decision = await decider.ainvoke(prompt)
    except Exception as exc:
        # Don't fail the whole turn on a tool-routing hiccup. The user
        # will get a generic answer; the next turn will try again.
        logger.warning("Tool decision LLM call failed: %s", exc)
        return ToolRunResult(observations="", tool_names=[])

    tool_calls = getattr(decision, "tool_calls", None) or []
    if not tool_calls:
        return ToolRunResult(observations="", tool_names=[])

    # Cap fan-out and dedupe by tool name (Gemini sometimes asks for the
    # same tool twice with empty args).
    seen: set[str] = set()
    selected: list[StructuredTool] = []
    by_name = {tool.name: tool for tool in tools}
    for call in tool_calls[:MAX_TOOL_CALLS]:
        name: Any = call.get("name") if isinstance(call, dict) else getattr(call, "name", None)
        if not isinstance(name, str) or name in seen:
            continue
        tool = by_name.get(name)
        if tool is None:
            continue
        seen.add(name)
        selected.append(tool)

    if not selected:
        return ToolRunResult(observations="", tool_names=[])

    logger.info("MwanaBot tool run: %s", [t.name for t in selected])

    results = await asyncio.gather(
        *(_execute_tool(tool) for tool in selected),
        return_exceptions=False,
    )

    chunks = [
        f"### Résultat de l'outil `{tool.name}` :\n{result}"
        for tool, result in zip(selected, results)
    ]

    return ToolRunResult(
        observations="\n\n".join(chunks),
        tool_names=[t.name for t in selected],
    )
