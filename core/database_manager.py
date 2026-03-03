import os
import sqlite3
from typing import Dict, List
import config
from core.monitor import get_system_logger

log = get_system_logger(__name__)

class NewsDatabase:
    """수집된 시장 뉴스를 SQLite 데이터베이스에 저장하고 관리하는 클래스."""

    DB_FILE = os.path.join(config.DATA_DIR, "market_news.db")

    def __init__(self):
        self.conn = sqlite3.connect(self.DB_FILE)
        self._create_table()

    def _create_table(self):
        with self.conn:
            self.conn.execute("""
                CREATE TABLE IF NOT EXISTS news (
                    id INTEGER PRIMARY KEY,
                    category TEXT,
                    datetime INTEGER,
                    headline TEXT,
                    source TEXT,
                    summary TEXT,
                    url TEXT,
                    related TEXT,
                    is_processed INTEGER DEFAULT 0,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            self.conn.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS idx_headline ON news (headline)"
            )

    def save_news(self, news_list: List[Dict]):
        if not news_list:
            return
        with self.conn:
            for news in news_list:
                self.conn.execute("""
                    INSERT OR IGNORE INTO news (id, category, datetime, headline, source, summary, url, related)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    news.get("id"), news.get("category"), news.get("datetime"),
                    news.get("headline", news.get("title", "")),
                    news.get("source"), news.get("summary", news.get("description", "")),
                    news.get("url"), news.get("related", "")
                ))
        log.info(f"Saved {len(news_list)} news articles to the database.")

    def get_unprocessed_news(self) -> List[Dict]:
        with self.conn:
            cursor = self.conn.execute(
                "SELECT * FROM news WHERE is_processed = 0 ORDER BY datetime DESC"
            )
            rows = cursor.fetchall()
            columns = [d[0] for d in cursor.description]
            return [dict(zip(columns, row)) for row in rows]

    def mark_news_as_processed(self, news_ids: List[int]):
        if not news_ids:
            return
        with self.conn:
            self.conn.execute(
                f"UPDATE news SET is_processed = 1 WHERE id IN ({','.join('?' for _ in news_ids)})",
                news_ids
            )
        log.info(f"Marked {len(news_ids)} news articles as processed.")

    def close(self):
        self.conn.close()
