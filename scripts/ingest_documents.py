from __future__ import annotations

import argparse
from pathlib import Path

from dotenv import load_dotenv
from langchain_text_splitters import RecursiveCharacterTextSplitter
from pypdf import PdfReader

from app.config import get_settings
from app.rag import RagStore


SUPPORTED_EXTENSIONS = {".txt", ".md", ".pdf"}


def read_document(file_path: Path) -> str:
    if file_path.suffix.lower() == ".pdf":
        reader = PdfReader(str(file_path))
        return "\n".join(page.extract_text() or "" for page in reader.pages)
    return file_path.read_text(encoding="utf-8")


def iter_input_files(path: Path):
    if path.is_file():
        yield path
        return
    yield from path.rglob("*")


def iter_document_texts(path: Path):
    splitter = RecursiveCharacterTextSplitter(chunk_size=1200, chunk_overlap=180)
    for file_path in iter_input_files(path):
        if not file_path.is_file() or file_path.suffix.lower() not in SUPPORTED_EXTENSIONS:
            continue
        text = read_document(file_path)
        for chunk_index, chunk in enumerate(splitter.split_text(text), start=1):
            yield chunk, {
                "source": str(file_path),
                "title": file_path.stem,
                "chunk": chunk_index,
            }


def main() -> None:
    parser = argparse.ArgumentParser(description="Ingère des documents EduFrais dans Pinecone.")
    parser.add_argument("path", type=Path, help="Fichier ou dossier contenant des documents .txt, .md ou .pdf.")
    parser.add_argument("--namespace", default=None, help="Namespace Pinecone optionnel.")
    parser.add_argument("--role", default=None, help="Role utilisateur associe au document.")
    args = parser.parse_args()

    load_dotenv()
    store = RagStore(get_settings())

    added = 0
    for text, metadata in iter_document_texts(args.path):
        if args.role:
            metadata["role"] = args.role
        added += store.add_texts([text], namespace=args.namespace, metadata=metadata)

    print(f"{added} document(s) ajoute(s) au RAG.")


if __name__ == "__main__":
    main()
