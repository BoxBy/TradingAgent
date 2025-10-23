import json
import os
from typing import Dict, List

try:
    __import__('pysqlite3')
    import sys
    sys.modules['sqlite3'] = sys.modules.pop('pysqlite3')
    print("✅ Successfully patched sqlite3 with pysqlite3.")
except ImportError:
    pass

import chromadb
import pandas as pd
from langchain_core.embeddings import Embeddings
import datetime

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
            name="stock_analysis_history", metadata={"hnsw:space": "cosine"}
        )
        self.news_collection = self.client.get_or_create_collection(
            name="news_archive", metadata={"hnsw:space": "cosine"}
        )
        # ✨ 새로운 컬렉션: 투자 경험(성공/실패) 기록용
        self.insights_collection = self.client.get_or_create_collection(
            name="trading_insights", metadata={"hnsw:space": "cosine"}
        )
        self.trade_log_collection = self.client.get_or_create_collection(
            name="trade_registration_logs", metadata={"hnsw:space": "cosine"}
        )
        self.critical_events_collection = self.client.get_or_create_collection(
            name="critical_trading_events",
            metadata={"hnsw:space": "cosine"}
        )
        log.info(f"RAG Manager initialized. DB path: {path}")

    def add_trade_logs_to_db(self, trade_logs: List[Dict]):
        """
        신규 매수 거래 정보 리스트를 데이터베이스에 추가합니다.
        """
        if not trade_logs:
            return
        try:
            documents = [json.dumps(log, default=str) for log in trade_logs]
            ids = [f"trade_{log['stock_code']}_{pd.Timestamp.now().isoformat()}_{i}" for i, log in enumerate(trade_logs)]
            embedding_texts = [f"Reasoning for buying {log['stock_code']}: {log.get('reasoning', '')}" for log in trade_logs]
            embeddings = self.embeddings.provider.embed_documents(embedding_texts)
            metadatas = [{"stock_code": log["stock_code"], "purchase_price": log.get("purchase_price", 0)} for log in trade_logs]

            self.trade_log_collection.add(
                ids=ids,
                embeddings=embeddings,
                documents=documents,
                metadatas=metadatas,
            )
            log.info(f"Added {len(trade_logs)} new trade logs to RAG DB.")
        except Exception as e:
            log.error(f"Error adding trade logs to RAG DB: {e}", exc_info=True)

    def retrieve_latest_trade_log(self, stock_code: str) -> Dict:
        """
        주어진 주식 코드에 대한 가장 최근의 거래 기록을 검색합니다.
        (get 메서드는 임베딩 검색이 아닌, ID나 metadata 필터링에 최적화되어 있습니다.)
        """
        log.info(f"Retrieving latest trade log for {stock_code} from RAG DB.")
        try:
            # 가장 최근 1개의 기록만 가져옵니다.
            results = self.trade_log_collection.get(
                where={"stock_code": stock_code}, limit=1, include=["documents"]
            )

            if results and results.get("documents"):
                # JSON 문자열을 다시 딕셔너리로 변환하여 반환
                latest_log_str = results["documents"][0]
                return json.loads(latest_log_str)
            return {}
        except Exception as e:
            log.error(
                f"Error retrieving trade log from RAG DB for {stock_code}: {e}",
                exc_info=True,
            )
            return {}

    def add_trading_insights(self, insights: List[Dict]):
        """
        성공 또는 실패한 거래에서 얻은 깨달음 리스트를 데이터베이스에 추가합니다.
        """
        if not insights:
            return
        try:
            documents = [insight['insight'] for insight in insights]
            ids = [f"insight_{insight['stock_code']}_{pd.Timestamp.now().isoformat()}_{i}" for i, insight in enumerate(insights)]
            embeddings = self.embeddings.provider.embed_documents(documents)
            metadatas = [{"stock_code": insight["stock_code"]} for insight in insights]

            self.insights_collection.add(
                ids=ids,
                embeddings=embeddings,
                documents=documents,
                metadatas=metadatas,
            )
            log.info(f"Added {len(insights)} new trading insights to RAG DB.")
        except Exception as e:
            log.error(f"Error adding trading insights to RAG DB: {e}", exc_info=True)

    def retrieve_relevant_insights(
        self, stock_code: str, query_text: str, n_results: int = 3
    ) -> List[str]:
        """
        주어진 쿼리와 가장 관련성 높은 과거의 투자 경험(깨달음)을 검색합니다.
        """
        log.info(
            f"Retrieving relevant trading insights for {stock_code} with query: '{query_text}'"
        )
        try:
            query_embedding = self.embeddings.provider.embed_query(query_text)
            results = self.insights_collection.query(
                query_embeddings=[query_embedding],
                n_results=n_results,
                where={"stock_code": stock_code},
            )

            if results and results.get("documents"):
                return results["documents"][0]
            return []
        except Exception as e:
            log.error(
                f"Error retrieving insights from RAG DB for {stock_code}: {e}",
                exc_info=True,
            )
            return []

    def add_analyses_to_db(self, analyses: List[Dict]):
        """분석 리포트 리스트를 데이터베이스에 추가합니다."""
        if not analyses:
            return
        try:
            documents = [analysis['report'] for analysis in analyses]
            ids = [f"analysis_{analysis['stock_code']}_{pd.Timestamp.now().isoformat()}_{i}" for i, analysis in enumerate(analyses)]
            embeddings = self.embeddings.provider.embed_documents(documents)
            metadatas = [{"stock_code": analysis["stock_code"]} for analysis in analyses]

            self.analysis_collection.add(
                ids=ids,
                embeddings=embeddings,
                documents=documents,
                metadatas=metadatas,
            )
            log.info(f"Added {len(analyses)} new analyses to RAG DB.")
        except Exception as e:
            log.error(f"Error adding analyses to RAG DB: {e}", exc_info=True)

    def retrieve_relevant_analysis(
        self, stock_code: str, query_text: str, n_results: int = 3
    ) -> List[Dict]:
        """주어진 쿼리와 가장 관련성 높은 과거 분석 내역을 검색합니다."""
        log.info(
            f"Retrieving relevant analysis for {stock_code} with query: '{query_text}'"
        )
        try:
            query_embedding = self.embeddings.provider.embed_query(query_text)
            results = self.analysis_collection.query(
                query_embeddings=[query_embedding],
                n_results=n_results,
                where={"stock_code": stock_code},
            )

            retrieved_docs = []
            if results.get("documents"):
                for doc_str in results["documents"][0]:
                    try:
                        retrieved_docs.append(json.loads(doc_str))
                    except json.JSONDecodeError:
                        log.warning(
                            f"Could not parse a document from RAG DB: {doc_str[:100]}..."
                        )
            return retrieved_docs
        except Exception as e:
            log.error(
                f"Error retrieving analysis from RAG DB for {stock_code}: {e}",
                exc_info=True,
            )
            return []

    def add_news_to_db(self, stock_code: str, news_list: List[Dict]):
        """뉴스 리스트를 뉴스 아카이브 데이터베이스에 추가합니다."""
        if not news_list:
            return
        log.info(f"Adding {len(news_list)} news articles for {stock_code} to RAG DB.")
        try:
            documents = [
                f"Headline: {news['headline']}\nSummary: {news['summary']}"
                for news in news_list
            ]
            metadatas = [
                {
                    "stock_code": stock_code,
                    "source": news.get("source"),
                    "datetime": pd.to_datetime(
                        news.get("datetime"), unit="s"
                    ).isoformat(),
                }
                for news in news_list
            ]
            ids = [f"news_{stock_code}_{news.get('id')}" for news in news_list]

            existing_ids_result = self.news_collection.get(ids=ids)
            existing_ids = set(existing_ids_result["ids"])

            new_docs, new_metas, new_ids = [], [], []
            if existing_ids:
                for doc, meta, id_ in zip(documents, metadatas, ids):
                    if id_ not in existing_ids:
                        new_docs.append(doc)
                        new_metas.append(meta)
                        new_ids.append(id_)
                if not new_ids:
                    return
            else:
                new_docs, new_metas, new_ids = documents, metadatas, ids

            news_embeddings = self.embeddings.provider.embed_documents(new_docs)
            self.news_collection.add(
                embeddings=news_embeddings,
                documents=new_docs,
                metadatas=new_metas,
                ids=new_ids,
            )
            log.info(
                f"Successfully added {len(new_ids)} new articles for {stock_code}."
            )
        except Exception as e:
            log.error(
                f"Error adding news to RAG DB for {stock_code}: {e}", exc_info=True
            )

    def retrieve_relevant_news(
        self, stock_code: str, query_text: str, n_results: int = 5
    ) -> List[Dict]:
        """주어진 쿼리와 가장 관련성 높은 과거 뉴스를 검색합니다."""
        log.info(
            f"Retrieving relevant news for {stock_code} with query: '{query_text}'"
        )
        try:
            query_embedding = self.embeddings.provider.embed_query(query_text)
            results = self.news_collection.query(
                query_embeddings=[query_embedding],
                n_results=n_results,
                where={"stock_code": stock_code},
            )

            if results and results["documents"]:
                return [
                    {"document": doc, "metadata": meta}
                    for doc, meta in zip(
                        results["documents"][0], results["metadatas"][0]
                    )
                ]
            return []
        except Exception as e:
            log.error(
                f"Error retrieving news from RAG DB for {stock_code}: {e}",
                exc_info=True,
            )
            return []
        
    def add_critical_events(self, events: List[Dict]):
        """중요 거래 결정(예: 심층 분석 매도) 리스트를 데이터베이스에 추가합니다."""
        if not events:
            return
        try:
            documents = [event['summary'] for event in events]
            ids = [f"event_{event['stock_code']}_{pd.Timestamp.now().isoformat()}_{i}" for i, event in enumerate(events)]
            embeddings = self.embeddings.provider.embed_documents(documents)
            metadatas = [{"stock_code": event["stock_code"], "timestamp": datetime.now().isoformat()} for event in events]
            
            self.critical_events_collection.add(
                ids=ids,
                embeddings=embeddings,
                documents=documents,
                metadatas=metadatas
            )
            log.info(f"Added {len(events)} critical events to RAG DB.")
        except Exception as e:
            log.error(f"Error adding critical events to RAG DB: {e}", exc_info=True)

    def retrieve_recent_critical_events(self, stock_codes: List[str], days: int = 7) -> str:
        """주어진 종목들에 대해 지정된 기간 내의 중요 이벤트를 검색하여 문자열로 반환합니다."""
        if not stock_codes:
            return "No critical events to check."
        
        log.info(f"Retrieving recent critical events for: {stock_codes}")
        try:
            # 기간 필터링은 ChromaDB의 where 필터로는 직접 지원되지 않으므로,
            # 최근 데이터를 충분히 가져와서 코드 내에서 필터링합니다.
            results = self.critical_events_collection.get(
                where={"stock_code": {"$in": stock_codes}},
                include=["metadatas", "documents"],
                limit=len(stock_codes) * 5 # 각 종목당 최근 5개 정도의 이벤트를 가져옴
            )
            
            recent_events = []
            cutoff_date = datetime.datetime.now() - datetime.timedelta(days=days)

            for i, metadata in enumerate(results['metadatas']):
                event_time = datetime.date.fromisoformat(metadata['timestamp'])
                if event_time > cutoff_date:
                    recent_events.append(f"- {results['documents'][i]}")

            if not recent_events:
                return "No recent critical events found for the given stocks."
                
            return "\n".join(recent_events)
        except Exception as e:
            log.error(f"Error retrieving critical events from RAG DB: {e}", exc_info=True)
            return "Error retrieving critical events."