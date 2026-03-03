import json
import numpy as np
from typing import List, Dict, Any
from sentence_transformers import SentenceTransformer

class AdvancedRAG:
    """
    High-Performance RAG layer using Embeddings and Taxonomy Filtering.
    """
    def __init__(self):
        # Loading the local snowflake model as used in TradingAgent
        self.embedding_model = SentenceTransformer("dragonkue/snowflake-arctic-embed-l-v2.0-ko")
        self.documents = []  # In-memory store: {"id": str, "text": str, "embedding": list, "metadata": dict}
        
    async def get_embedding(self, text: str) -> List[float]:
        try:
            # Generate local embedding synchronously (blocks thread minimally for short text)
            embedding = self.embedding_model.encode(text, convert_to_numpy=True)
            return embedding.tolist()
        except Exception as e:
            print(f"[RAG] Embedding Error: {e}")
            return []

    def cosine_similarity(self, a: List[float], b: List[float]) -> float:
        a_np = np.array(a)
        b_np = np.array(b)
        if np.linalg.norm(a_np) == 0 or np.linalg.norm(b_np) == 0:
            return 0.0
        return float(np.dot(a_np, b_np) / (np.linalg.norm(a_np) * np.linalg.norm(b_np)))

    async def ingest_documents(self, docs: List[Dict[str, Any]]):
        """
        Docs format: [{"id": "1", "text": "News content...", "metadata": {"sector": "tech", "ticker": "AAPL"}}]
        """
        for doc in docs:
            emb = await self.get_embedding(doc["text"])
            self.documents.append({
                "id": doc.get("id", str(len(self.documents))),
                "text": doc["text"],
                "metadata": doc.get("metadata", {}),
                "embedding": emb
            })
            
    async def search(self, query: str, top_k: int = 5, filter_taxonomy: dict = None) -> List[Dict[str, Any]]:
        """
        Hybrid search: Taxonomic filter first, then semantic vector search.
        """
        query_emb = await self.get_embedding(query)
        if not query_emb:
            return []
            
        results = []
        for doc in self.documents:
            # 1. Taxonomic Hard Filter
            if filter_taxonomy:
                match = True
                for k, v in filter_taxonomy.items():
                    if doc["metadata"].get(k) != v:
                        match = False
                        break
                if not match: continue
                
            # 2. Semantic Score
            score = self.cosine_similarity(query_emb, doc["embedding"])
            results.append({
                "score": score,
                "text": doc["text"],
                "metadata": doc["metadata"]
            })
            
        # Sort by score descending
        results.sort(key=lambda x: x["score"], reverse=True)
        return results[:top_k]

# Global instance
rag_system = AdvancedRAG()

# Tool schema for Teammates
RAG_TOOLS_SCHEMA = [
    {
        "type": "function",
        "function": {
            "name": "SearchMarketNews",
            "description": "Perform an advanced semantic search on the latest market news and reports.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "The search query (e.g., 'AAPL earnings impact')."},
                    "sector_filter": {"type": "string", "description": "Optional sector taxonomy filter (e.g., 'tech', 'defense')."}
                },
                "required": ["query"]
            }
        }
    }
]

async def tool_search_market_news(tool_name: str, arguments: dict):
    query = arguments.get("query")
    sector = arguments.get("sector_filter")
    filters = {"sector": sector} if sector else None
    
    results = await rag_system.search(query, top_k=5, filter_taxonomy=filters)
    if not results:
        return "No relevant news found."
        
    output = "RAG Search Results:\n"
    for idx, r in enumerate(results):
        output += f"{idx+1}. [Score: {r['score']:.2f}] {r['text']} (Meta: {r['metadata']})\n"
    return output

def register_rag_tools(agent):
    """Registers RAG tools to a TradingAgentCore instance."""
    agent.add_tool(RAG_TOOLS_SCHEMA[0], tool_search_market_news)
