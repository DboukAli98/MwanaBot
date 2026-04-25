from collections.abc import Iterable
from typing import Any
from uuid import uuid4

from langchain_core.documents import Document
from langchain_google_genai import GoogleGenerativeAIEmbeddings
from langchain_pinecone import PineconeVectorStore
from pinecone import Pinecone, ServerlessSpec

from app.config import Settings


EMBEDDING_MODEL = "models/text-embedding-004"
EMBEDDING_DIMENSION = 768


class RagStore:
    def __init__(self, settings: Settings) -> None:
        if not settings.pinecone_api_key:
            raise RuntimeError("PINECONE_API_KEY est requis pour utiliser le RAG.")

        self.settings = settings
        self.embeddings = GoogleGenerativeAIEmbeddings(
            model=EMBEDDING_MODEL,
            google_api_key=settings.google_api_key,
        )
        self.client = Pinecone(api_key=settings.pinecone_api_key)
        self._ensure_index()
        self.vectorstore = PineconeVectorStore(
            index_name=settings.pinecone_index_name,
            embedding=self.embeddings,
            pinecone_api_key=settings.pinecone_api_key,
        )

    def _ensure_index(self) -> None:
        indexes = self.client.list_indexes()
        try:
            existing = set(indexes.names())
        except AttributeError:
            existing = {index["name"] for index in indexes}
        if self.settings.pinecone_index_name in existing:
            return

        self.client.create_index(
            name=self.settings.pinecone_index_name,
            dimension=EMBEDDING_DIMENSION,
            metric="cosine",
            spec=ServerlessSpec(
                cloud=self.settings.pinecone_cloud,
                region=self.settings.pinecone_region,
            ),
        )

    def add_texts(
        self,
        texts: Iterable[str],
        namespace: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> int:
        docs = [
            Document(
                page_content=text,
                metadata={**(metadata or {}), "source_id": str(uuid4())},
            )
            for text in texts
            if text.strip()
        ]
        if not docs:
            return 0
        self.vectorstore.add_documents(docs, namespace=namespace)
        return len(docs)

    def search(self, query: str, namespace: str | None = None, k: int = 4) -> list[Document]:
        return self.vectorstore.similarity_search(query, k=k, namespace=namespace)


def format_documents(documents: list[Document]) -> str:
    if not documents:
        return "Aucun contexte documentaire pertinent n'a été trouvé."

    chunks = []
    for index, doc in enumerate(documents, start=1):
        title = doc.metadata.get("title") or doc.metadata.get("source") or f"Document {index}"
        chunks.append(f"[{index}] {title}\n{doc.page_content}")
    return "\n\n".join(chunks)
