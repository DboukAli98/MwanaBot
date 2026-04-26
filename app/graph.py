from typing import TypedDict

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_google_genai import ChatGoogleGenerativeAI
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, StateGraph

from app.config import Settings
from app.memory import session_memory
from app.prompts import SYSTEM_PROMPT
from app.rag import RagStore, format_documents


class MwanaBotState(TypedDict, total=False):
    question: str
    namespace: str | None
    thread_id: str
    username: str | None
    role: str | None
    context: str
    sources: list[dict]
    answer: str
    # Pre-resolved SchoolFees data — populated upstream by main.chat()
    # before the graph runs. The generate node folds it into the prompt
    # so the LLM grounds its answer in the user's real account data.
    tool_observations: str


def format_history(thread_id: str) -> str:
    history = session_memory.get(thread_id)
    if not history:
        return "Aucun historique."
    return "\n".join(f"{message['role']}: {message['content']}" for message in history[-10:])


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
                "context": "Le RAG Pinecone n'est pas encore configure.",
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
        username = state.get("username") or "Non fourni"
        role = state.get("role") or state.get("namespace") or "Non fourni"
        tool_observations = state.get("tool_observations") or ""
        tool_section = (
            f"Données récupérées via les outils SchoolFees :\n{tool_observations}\n\n"
            if tool_observations
            else ""
        )
        prompt = (
            f"Utilisateur authentifié:\nNom: {username}\nRôle: {role}\n\n"
            f"Historique récent de la conversation:\n{format_history(state['thread_id'])}\n\n"
            f"Question utilisateur:\n{state['question']}\n\n"
            f"{tool_section}"
            f"Contexte RAG disponible:\n{state.get('context', '')}\n\n"
            "Réponds comme MwanaBot, en français, avec une aide concrète. "
            "Si les données outils ci-dessus contiennent la réponse, base-toi "
            "dessus en priorité (ce sont des données réelles du compte de "
            "l'utilisateur). N'invente jamais de chiffre, date ou nom qui "
            "ne figurerait pas dans les données outils ou le contexte RAG."
        )
        response = await llm.ainvoke([SystemMessage(content=SYSTEM_PROMPT), HumanMessage(content=prompt)])
        answer = str(response.content)
        session_memory.append(state["thread_id"], "assistant", answer)
        return {**state, "answer": answer}

    workflow = StateGraph(MwanaBotState)
    workflow.add_node("retrieve", retrieve)
    workflow.add_node("generate", generate)
    workflow.set_entry_point("retrieve")
    workflow.add_edge("retrieve", "generate")
    workflow.add_edge("generate", END)
    return workflow.compile(checkpointer=MemorySaver())
