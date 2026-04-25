from typing import TypedDict

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_google_genai import ChatGoogleGenerativeAI
from langgraph.graph import END, StateGraph

from app.config import Settings
from app.prompts import SYSTEM_PROMPT
from app.rag import RagStore, format_documents


class MwanaBotState(TypedDict, total=False):
    question: str
    namespace: str | None
    context: str
    sources: list[dict]
    answer: str


def build_graph(settings: Settings):
    llm = ChatGoogleGenerativeAI(
        model=settings.gemini_model,
        google_api_key=settings.google_api_key,
        temperature=0.35,
    )

    rag_store = RagStore(settings) if settings.pinecone_api_key else None

    async def retrieve(state: MwanaBotState) -> MwanaBotState:
        if rag_store is None:
            return {
                **state,
                "context": "Le RAG Pinecone n'est pas encore configuré.",
                "sources": [],
            }

        docs = rag_store.search(state["question"], namespace=state.get("namespace"))
        return {
            **state,
            "context": format_documents(docs),
            "sources": [
                {
                    "content": doc.page_content,
                    "metadata": doc.metadata,
                }
                for doc in docs
            ],
        }

    async def generate(state: MwanaBotState) -> MwanaBotState:
        prompt = (
            f"Question utilisateur:\n{state['question']}\n\n"
            f"Contexte RAG disponible:\n{state.get('context', '')}\n\n"
            "Réponds comme MwanaBot, en français, avec une aide concrète."
        )
        response = await llm.ainvoke([SystemMessage(content=SYSTEM_PROMPT), HumanMessage(content=prompt)])
        return {**state, "answer": str(response.content)}

    workflow = StateGraph(MwanaBotState)
    workflow.add_node("retrieve", retrieve)
    workflow.add_node("generate", generate)
    workflow.set_entry_point("retrieve")
    workflow.add_edge("retrieve", "generate")
    workflow.add_edge("generate", END)
    return workflow.compile()

