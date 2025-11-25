import os
from typing import List

try:
    __import__('pysqlite3')
    import sys
    sys.modules['sqlite3'] = sys.modules.pop('pysqlite3')
    print("✅ Successfully patched sqlite3 with pysqlite3.")
except ImportError:
    pass

import chromadb
from sentence_transformers import SentenceTransformer

from src import config


COLLECTION_NAMES: List[str] = [
    "stock_analysis_history",
    "news_archive",
    "trading_insights",
    "trade_registration_logs",
    "critical_trading_events",
]


def migrate_collection(model, old_collection, new_collection, name: str) -> None:
    print(f"\n=== Migrating collection: {name} ===")

    try:
        results = old_collection.get(include=["ids", "documents", "metadatas"])
    except Exception as e:
        print(f"[WARN] Failed to read from old collection '{name}': {e}")
        return

    ids = results.get("ids") or []
    documents = results.get("documents") or []
    metadatas = results.get("metadatas") or []

    if not ids:
        print(f"[INFO] No documents found in old collection '{name}'. Skipping.")
        return

    print(f"[INFO] Found {len(ids)} documents in old collection '{name}'.")

    # Avoid duplicating if the new collection already has some of these IDs
    try:
        existing = new_collection.get(ids=ids)
        existing_ids = set(existing.get("ids") or [])
    except Exception:
        existing_ids = set()

    to_add_ids = []
    to_add_docs = []
    to_add_metas = []

    for _id, doc, meta in zip(ids, documents, metadatas):
        if _id in existing_ids:
            continue
        if doc is None:
            continue
        to_add_ids.append(_id)
        to_add_docs.append(doc)
        to_add_metas.append(meta)

    if not to_add_ids:
        print(f"[INFO] All documents for '{name}' already exist in new collection. Skipping add.")
        return

    print(f"[INFO] Re-embedding {len(to_add_ids)} documents for '{name}' with snowflake model...")
    embeddings = model.encode(to_add_docs, convert_to_numpy=True).tolist()

    new_collection.add(
        ids=to_add_ids,
        documents=to_add_docs,
        metadatas=to_add_metas,
        embeddings=embeddings,
    )

    print(f"[DONE] Added {len(to_add_ids)} documents to new collection '{name}'.")


def main() -> None:
    project_root = config.PROJECT_ROOT
    old_path = os.path.join(project_root, "rag_database_gemini_backup")
    new_path = config.RAG_DB_PATH

    print(f"PROJECT_ROOT: {project_root}")
    print(f"Old (Gemini) DB path: {old_path}")
    print(f"New (snowflake) DB path: {new_path}")

    if not os.path.isdir(old_path):
        raise SystemExit(
            "Old RAG DB directory 'rag_database_gemini_backup' does not exist. "
            "Please rename your current 'rag_database' directory to 'rag_database_gemini_backup' first."
        )

    os.makedirs(new_path, exist_ok=True)

    print("\n[INFO] Loading SentenceTransformer model: dragonkue/snowflake-arctic-embed-l-v2.0-ko")
    model = SentenceTransformer("dragonkue/snowflake-arctic-embed-l-v2.0-ko")

    old_client = chromadb.PersistentClient(path=old_path)
    new_client = chromadb.PersistentClient(path=new_path)

    for name in COLLECTION_NAMES:
        old_collection = old_client.get_or_create_collection(
            name=name, metadata={"hnsw:space": "cosine"}
        )
        new_collection = new_client.get_or_create_collection(
            name=name, metadata={"hnsw:space": "cosine"}
        )
        migrate_collection(model, old_collection, new_collection, name)

    print("\nAll collections processed. Migration complete.")


if __name__ == "__main__":
    main()
