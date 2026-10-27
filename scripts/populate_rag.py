"""Run full RAG inventory population."""
from app.rag.populator import populate_rag_database

if __name__ == "__main__":
    counts = populate_rag_database(days=365)
    print(f"RAG Population Completed: {counts}")
