import json
from collections.abc import AsyncIterator

from fastapi import FastAPI
from fastapi.responses import StreamingResponse
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_google_genai import ChatGoogleGenerativeAI

from app.config import get_settings
from app.graph import build_graph
from app.models import ChatRequest, ChatResponse, IngestResponse, IngestTextRequest, Source
from app.prompts import SYSTEM_PROMPT
from app.rag import RagStore

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
    version="0.1.0",
)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "bot": "MwanaBot"}


def sse_event(event: str, data: dict) -> str:
    payload = json.dumps(data, ensure_ascii=False)
    return f"event: {event}\ndata: {payload}\n\n"


def build_stream_prompt(question: str, context: str) -> list:
    prompt = (
        f"Question utilisateur:\n{question}\n\n"
        f"Contexte RAG disponible:\n{context}\n\n"
        "Réponds comme MwanaBot, en français, avec une aide concrète."
    )
    return [SystemMessage(content=SYSTEM_PROMPT), HumanMessage(content=prompt)]


async def stream_chat_events(request: ChatRequest) -> AsyncIterator[str]:
    try:
        yield sse_event(
            "start",
            {
                "conversation_id": request.conversation_id,
                "bot": "MwanaBot",
            },
        )

        rag_store = RagStore(settings) if settings.pinecone_api_key else None
        if rag_store is None:
            documents = []
            context = "Le RAG Pinecone n'est pas encore configuré."
        else:
            documents = rag_store.search(
                request.message,
                namespace=request.metadata.get("namespace"),
            )
            context = "\n\n".join(
                f"[{index}] {doc.metadata.get('title') or doc.metadata.get('source') or 'Document'}\n"
                f"{doc.page_content}"
                for index, doc in enumerate(documents, start=1)
            )

        sources = [
            {
                "content": doc.page_content,
                "metadata": doc.metadata,
            }
            for doc in documents
        ]
        yield sse_event("sources", {"sources": sources})

        full_answer = ""
        async for chunk in stream_llm.astream(build_stream_prompt(request.message, context)):
            token = str(chunk.content or "")
            if not token:
                continue
            full_answer += token
            yield sse_event("token", {"content": token})

        yield sse_event(
            "done",
            {
                "answer": full_answer,
                "conversation_id": request.conversation_id,
            },
        )
    except Exception as exc:
        yield sse_event(
            "error",
            {
                "message": "MwanaBot n'a pas pu terminer la réponse.",
                "detail": str(exc),
            },
        )


@app.post("/chat", response_model=ChatResponse)
async def chat(request: ChatRequest) -> ChatResponse:
    result = await graph.ainvoke(
        {
            "question": request.message,
            "namespace": request.metadata.get("namespace"),
        }
    )
    return ChatResponse(
        answer=result["answer"],
        conversation_id=request.conversation_id,
        sources=[
            Source(
                content=source["content"],
                metadata=source.get("metadata", {}),
            )
            for source in result.get("sources", [])
        ],
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
