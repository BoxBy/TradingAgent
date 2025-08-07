import chromadb
from langchain_core.embeddings import Embeddings
import os
import pandas as pd
import json
from typing import List, Dict

from .. import config
from ..utils import logger

log = logger.get_logger(__name__)

class RAGManager:
    def __init__(self, embeddings: Embeddings, path: str = config.RAG_DB_PATH):
        if not os.path.exists(path):
            os.makedirs(path)
        self.client = chromadb.PersistentClient(path=path)
        self.embeddings = embeddings
        
        self.analysis_collection = self.client.get_or_create_collection(
            name="stock_analysis_history",
            metadata={"hnsw:space": "cosine"}
        )
        self.news_collection = self.client.get_or_create_collection(
            name="news_archive",
            metadata={"hnsw:space": "cosine"}
        )
        # ✨ 새로운 컬렉션: 투자 경험(성공/실패) 기록용
        self.insights_collection = self.client.get_or_create_collection(
            name="trading_insights",
            metadata={"hnsw:space": "cosine"}
        )
        log.info(f"RAG Manager initialized. DB path: {path}")

    # ✨ 새로운 함수: 투자 경험(깨달음)을 DB에 추가
    def add_trading_insight(self, stock_code: str, insight: str):
        """
        성공 또는 실패한 거래에서 얻은 깨달음을 데이터베이스에 추가합니다.
        :param stock_code: 관련 주식 코드
        :param insight: "성공 요인: ...", "실패 요인: ..." 형태의 문자열
        """
        try:
            insight_id = f"insight_{stock_code}_{pd.Timestamp.now().isoformat()}"
            embedding = self.embeddings.embed_query(insight)
            
            self.insights_collection.add(
                ids=[insight_id],
                embeddings=[embedding],
                documents=[insight],
                metadatas=[{"stock_code": stock_code}]
            )
            log.info(f"Added new trading insight for {stock_code} to RAG DB.")
        except Exception as e:
            log.error(f"Error adding trading insight to RAG DB for {stock_code}: {e}", exc_info=True)

    # ✨ 새로운 함수: 현재 분석과 관련된 과거의 투자 경험 검색
    def retrieve_relevant_insights(self, stock_code: str, query_text: str, n_results: int = 3) -> List[str]:
        """
        주어진 쿼리와 가장 관련성 높은 과거의 투자 경험(깨달음)을 검색합니다.
        """
        log.info(f"Retrieving relevant trading insights for {stock_code} with query: '{query_text}'")
        try:
            query_embedding = self.embeddings.embed_query(query_text)
            results = self.insights_collection.query(
                query_embeddings=[query_embedding],
                n_results=n_results,
                where={"stock_code": stock_code}
            )
            
            if results and results.get('documents'):
                return results['documents'][0]
            return []
        except Exception as e:
            log.error(f"Error retrieving insights from RAG DB for {stock_code}: {e}", exc_info=True)
            return []

    def add_analysis_to_db(self, stock_code: str, analysis_report: str):
        """분석 리포트를 데이터베이스에 추가합니다."""
        try:
            report_id = f"analysis_{stock_code}_{pd.Timestamp.now().isoformat()}"
            embedding = self.embeddings.embed_query(analysis_report)
            
            self.analysis_collection.add(
                ids=[report_id],
                embeddings=[embedding],
                documents=[analysis_report],
                metadatas=[{"stock_code": stock_code}]
            )
            log.info(f"Added analysis for {stock_code} to RAG DB.")
        except Exception as e:
            log.error(f"Error adding analysis to RAG DB for {stock_code}: {e}", exc_info=True)

    def retrieve_relevant_analysis(self, stock_code: str, query_text: str, n_results: int = 3) -> List[Dict]:
        """주어진 쿼리와 가장 관련성 높은 과거 분석 내역을 검색합니다."""
        log.info(f"Retrieving relevant analysis for {stock_code} with query: '{query_text}'")
        try:
            query_embedding = self.embeddings.embed_query(query_text)
            results = self.analysis_collection.query(
                query_embeddings=[query_embedding],
                n_results=n_results,
                where={"stock_code": stock_code}
            )
            
            retrieved_docs = []
            if results.get('documents'):
                for doc_str in results['documents'][0]:
                    try:
                        retrieved_docs.append(json.loads(doc_str))
                    except json.JSONDecodeError:
                        log.warning(f"Could not parse a document from RAG DB: {doc_str[:100]}...")
            return retrieved_docs
        except Exception as e:
            log.error(f"Error retrieving analysis from RAG DB for {stock_code}: {e}", exc_info=True)
            return []

    def add_news_to_db(self, stock_code: str, news_list: List[Dict]):
        """뉴스 리스트를 뉴스 아카이브 데이터베이스에 추가합니다."""
        if not news_list: return
        log.info(f"Adding {len(news_list)} news articles for {stock_code} to RAG DB.")
        try:
            documents = [f"Headline: {news['headline']}\nSummary: {news['summary']}" for news in news_list]
            metadatas = [{"stock_code": stock_code, "source": news.get('source'), "datetime": pd.to_datetime(news.get('datetime'), unit='s').isoformat()} for news in news_list]
            ids = [f"news_{stock_code}_{news.get('id')}" for news in news_list]
            
            existing_ids_result = self.news_collection.get(ids=ids)
            existing_ids = set(existing_ids_result['ids'])
            
            new_docs, new_metas, new_ids = [], [], []
            if existing_ids:
                for doc, meta, id_ in zip(documents, metadatas, ids):
                    if id_ not in existing_ids:
                        new_docs.append(doc); new_metas.append(meta); new_ids.append(id_)
                if not new_ids: return
            else:
                new_docs, new_metas, new_ids = documents, metadatas, ids

            news_embeddings = self.embeddings.embed_documents(new_docs)
            self.news_collection.add(embeddings=news_embeddings, documents=new_docs, metadatas=new_metas, ids=new_ids)
            log.info(f"Successfully added {len(new_ids)} new articles for {stock_code}.")
        except Exception as e:
            log.error(f"Error adding news to RAG DB for {stock_code}: {e}", exc_info=True)

    def retrieve_relevant_news(self, stock_code: str, query_text: str, n_results: int = 5) -> List[Dict]:
        """주어진 쿼리와 가장 관련성 높은 과거 뉴스를 검색합니다."""
        log.info(f"Retrieving relevant news for {stock_code} with query: '{query_text}'")
        try:
            query_embedding = self.embeddings.embed_query(query_text)
            results = self.news_collection.query(
                query_embeddings=[query_embedding],
                n_results=n_results,
                where={"stock_code": stock_code}
            )
            
            if results and results['documents']:
                return [{"document": doc, "metadata": meta} for doc, meta in zip(results['documents'][0], results['metadatas'][0])]
            return []
        except Exception as e:
            log.error(f"Error retrieving news from RAG DB for {stock_code}: {e}", exc_info=True)
            return []