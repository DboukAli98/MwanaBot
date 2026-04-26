import json
import logging
from collections.abc import AsyncIterator
from typing import Any

from fastapi import FastAPI
from fastapi.responses import StreamingResponse
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_google_genai import ChatGoogleGenerativeAI

from app.config import get_settings
from app.graph import build_graph
from app.memory import session_memory
from app.models import ChatRequest, ChatResponse, IngestResponse, IngestTextRequest, Source
from app.prompts import SYSTEM_PROMPT
from app.rag import RagStore
from app.tool_runner import ToolRunResult, decide_and_run_tools
from app.tools import ParentToolBundle, build_parent_tools, serialize_results


logger = logging.getLogger("mwanabot.main")
settings = get_settings()
graph = build_graph(settings)
stream_llm = ChatGoogleGenerativeAI(
    model=settings.gemini_model,
    google_api_key=settings.google_api_key,
    temperature=0.35,
)

app = FastAPI(
    title="MwanaBot API",
    description="Assistant RAG francophone pour EduFrais.",
    version="0.2.0",
)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "bot": "MwanaBot"}


# ---------------------------------------------------------------------------
# Helpers shared between /chat and /chat/stream
# ---------------------------------------------------------------------------


def get_thread_id(request: ChatRequest) -> str:
    return request.conversation_id or request.user_id or "anonymous"


def sse_event(event: str, data: dict) -> str:
    payload = json.dumps(data, ensure_ascii=False)
    return f"event: {event}\ndata: {payload}\n\n"


def format_history(thread_id: str) -> str:
    history = session_memory.get(thread_id)
    if not history:
        return "Aucun historique."
    return "\n".join(f"{message['role']}: {message['content']}" for message in history[-10:])


def _coerce_int(value: Any) -> int | None:
    """Mobile may stringify numeric ids — coerce gracefully and return
    None when the value is empty / unparseable."""
    if value is None:
        return None
    try:
        n = int(value)
        return n if n > 0 else None
    except (TypeError, ValueError):
        return None


def _extract_user_context(metadata: dict[str, Any]) -> dict[str, Any]:
    """Pulls the per-user keys we care about out of the chat-request
    metadata. The mobile client is the source of truth for these — the
    backend trusts what's sent because it's already authenticated by the
    bearer token, which the same metadata carries."""
    return {
        "auth_token": metadata.get("auth_token"),
        "parent_id": _coerce_int(metadata.get("parent_id")),
        "school_id": _coerce_int(metadata.get("school_id")),
        "role": (metadata.get("role") or "").lower() or None,
        "username": metadata.get("username"),
    }


async def _resolve_tools(
    request: ChatRequest,
) -> tuple[ToolRunResult, ParentToolBundle]:
    """Build role-appropriate tools, ask the LLM what to call, and run.

    Returns both the LLM-side run summary AND the bundle, so the caller
    can read `bundle.results` for the structured payloads emitted as
    `tool_result` SSE events.
    """
    ctx = _extract_user_context(request.metadata or {})
    empty_bundle = ParentToolBundle(tools=[], results=[])

    # For now only parents have tools. Other roles fall through to plain
    # RAG until their toolsets land.
    if ctx["role"] != "parent":
        return ToolRunResult(observations="", tool_names=[]), empty_bundle

    bundle = build_parent_tools(
        settings,
        auth_token=ctx["auth_token"],
        parent_id=ctx["parent_id"],
        school_id=ctx["school_id"],
    )
    if not bundle.tools:
        return ToolRunResult(observations="", tool_names=[]), empty_bundle

    run = await decide_and_run_tools(settings, question=request.message, tools=bundle.tools)
    return run, bundle


def _build_final_prompt(
    request: ChatRequest,
    *,
    rag_context: str,
    tool_observations: str,
    thread_id: str,
) -> list:
    metadata = request.metadata or {}
    username = metadata.get("username") or "Non fourni"
    role = metadata.get("role") or metadata.get("namespace") or "Non fourni"

    tool_section = (
        f"Données récupérées via les outils SchoolFees :\n{tool_observations}\n\n"
        if tool_observations
        else ""
    )

    prompt = (
        f"Utilisateur authentifié:\nNom: {username}\nRôle: {role}\n\n"
        f"Historique récent de la conversation:\n{format_history(thread_id)}\n\n"
        f"Question utilisateur:\n{request.message}\n\n"
        f"{tool_section}"
        f"Contexte RAG disponible:\n{rag_context}\n\n"
        "Réponds comme MwanaBot, en français, avec une aide concrète. "
        "Si les données outils ci-dessus contiennent la réponse, base-toi "
        "dessus en priorité (ce sont des données réelles du compte de "
        "l'utilisateur). N'invente jamais de chiffre, date ou nom qui "
        "ne figurerait pas dans les données outils ou le contexte RAG."
    )
    return [SystemMessage(content=SYSTEM_PROMPT), HumanMessage(content=prompt)]


# ---------------------------------------------------------------------------
# /chat/stream — main entry point used by the mobile MwanaBot screen
# ---------------------------------------------------------------------------


async def stream_chat_events(request: ChatRequest) -> AsyncIterator[str]:
    thread_id = get_thread_id(request)
    try:
        session_memory.append(thread_id, "user", request.message)
        yield sse_event(
            "start",
            {
                "conversation_id": thread_id,
                "bot": "MwanaBot",
            },
        )

        # 1. RAG retrieval (same as before).
        rag_store = RagStore(settings) if settings.pinecone_api_key else None
        if rag_store is None:
            documents = []
            rag_context = "Le RAG Pinecone n'est pas encore configuré."
        else:
            documents = rag_store.search(
                request.message,
                namespace=(request.metadata or {}).get("namespace"),
            )
            rag_context = "\n\n".join(
                f"[{index}] {doc.metadata.get('title') or doc.metadata.get('source') or 'Document'}\n"
                f"{doc.page_content}"
                for index, doc in enumerate(documents, start=1)
            ) or "Aucun document RAG pertinent."

        sources = [
            {"content": doc.page_content, "metadata": doc.metadata}
            for doc in documents
        ]
        yield sse_event("sources", {"sources": sources})

        # 2. Phase 1+2: pick tools, execute them, get observations.
        tool_result, bundle = await _resolve_tools(request)
        if tool_result.tool_names:
            yield sse_event("tools", {"tools": tool_result.tool_names})

        # Emit one `tool_result` event per executed tool BEFORE we start
        # streaming tokens. The mobile renders these as cards above /
        # below the bot's prose answer, with action buttons that
        # deep-link into the relevant screen (`/(app)/payments`, etc.).
        serialized_results = serialize_results(bundle.results)
        for tool_payload in serialized_results:
            yield sse_event("tool_result", tool_payload)

        # 3. Stream the final answer with both contexts injected.
        full_answer = ""
        prompt = _build_final_prompt(
            request,
            rag_context=rag_context,
            tool_observations=tool_result.observations,
            thread_id=thread_id,
        )
        async for chunk in stream_llm.astream(prompt):
            token = str(chunk.content or "")
            if not token:
                continue
            full_answer += token
            yield sse_event("token", {"content": token})

        session_memory.append(thread_id, "assistant", full_answer)
        yield sse_event(
            "done",
            {
                "answer": full_answer,
                "conversation_id": thread_id,
                # Echo the tool results in the `done` event too so the
                # mobile client can re-attach them if it missed any
                # mid-stream events (e.g. screen backgrounded briefly).
                "tool_results": serialized_results,
            },
        )
    except Exception as exc:
        logger.exception("MwanaBot stream failed: %s", exc)
        yield sse_event(
            "error",
            {
                "message": "MwanaBot n'a pas pu terminer la réponse.",
                "detail": str(exc),
            },
        )


# ---------------------------------------------------------------------------
# /chat — non-streaming variant. Same logic; tool path runs upstream of
# the LangGraph generate node so RAG + tools both feed the final answer.
# ---------------------------------------------------------------------------


@app.post("/chat", response_model=ChatResponse)
async def chat(request: ChatRequest) -> ChatResponse:
    thread_id = get_thread_id(request)
    session_memory.append(thread_id, "user", request.message)

    tool_result, bundle = await _resolve_tools(request)

    metadata = request.metadata or {}
    result = await graph.ainvoke(
        {
            "question": request.message,
            "namespace": metadata.get("namespace"),
            "thread_id": thread_id,
            "username": metadata.get("username"),
            "role": metadata.get("role"),
            "tool_observations": tool_result.observations,
        },
        config={"configurable": {"thread_id": thread_id}},
    )
    return ChatResponse(
        answer=result["answer"],
        conversation_id=thread_id,
        sources=[
            Source(
                content=source["content"],
                metadata=source.get("metadata", {}),
            )
            for source in result.get("sources", [])
        ],
        tool_results=serialize_results(bundle.results),
    )


@app.post("/chat/stream")
async def chat_stream(request: ChatRequest) -> StreamingResponse:
    return StreamingResponse(
        stream_chat_events(request),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@app.post("/ingest/texts", response_model=IngestResponse)
def ingest_texts(request: IngestTextRequest) -> IngestResponse:
    store = RagStore(settings)
    added = store.add_texts(
        request.texts,
        namespace=request.namespace,
        metadata=request.metadata,
    )
    return IngestResponse(added=added, namespace=request.namespace)
