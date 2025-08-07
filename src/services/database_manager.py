import sqlite3
import os
from typing import List, Dict

from .. import config
from ..utils import logger

log = logger.get_logger(__name__)

class NewsDatabase:
    """
    수집된 시장 뉴스를 SQLite 데이터베이스에 저장하고 관리하는 클래스.
    """
    DB_FILE = os.path.join(config.DATA_DIR, "market_news.db")

    def __init__(self):
        self.conn = sqlite3.connect(self.DB_FILE)
        self._create_table()

    def _create_table(self):
        # 뉴스 데이터를 저장할 테이블 생성
        with self.conn:
            self.conn.execute("""
                CREATE TABLE IF NOT EXISTS news (
                    id INTEGER PRIMARY KEY,
                    category TEXT,
                    datetime INTEGER,
                    headline TEXT,
                    image TEXT,
                    related TEXT,
                    source TEXT,
                    summary TEXT,
                    url TEXT,
                    is_processed INTEGER DEFAULT 0,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            # 중복 저장을 방지하기 위해 headline에 UNIQUE 인덱스 생성
            self.conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_headline ON news (headline)")

    def save_news(self, news_list: List[Dict]):
        """
        새로운 뉴스 목록을 데이터베이스에 저장합니다. 중복된 헤드라인은 무시됩니다.
        """
        if not news_list:
            return
        
        with self.conn:
            for news in news_list:
                # headline이 중복될 경우 무시하고 넘어감 (IGNORE)
                self.conn.execute("""
                    INSERT OR IGNORE INTO news (id, category, datetime, headline, image, related, source, summary, url)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    news.get('id'), news.get('category'), news.get('datetime'), news.get('headline'),
                    news.get('image'), news.get('related'), news.get('source'),
                    news.get('summary'), news.get('url')
                ))
        log.info(f"Saved {len(news_list)} news articles to the database.")

    def get_unprocessed_news(self) -> List[Dict]:
        """
        아직 처리되지 않은(is_processed = 0) 모든 뉴스를 가져옵니다.
        """
        with self.conn:
            cursor = self.conn.execute("SELECT * FROM news WHERE is_processed = 0 ORDER BY datetime DESC")
            rows = cursor.fetchall()
            # SQLite 결과를 딕셔너리 리스트로 변환
            columns = [description[0] for description in cursor.description]
            return [dict(zip(columns, row)) for row in rows]

    def mark_news_as_processed(self, news_ids: List[int]):
        """
        주어진 ID 목록에 해당하는 뉴스들을 '처리됨'(is_processed = 1)으로 표시합니다.
        """
        if not news_ids:
            return
        
        with self.conn:
            # 여러 ID를 한번에 업데이트
            self.conn.execute(f"""
                UPDATE news
                SET is_processed = 1
                WHERE id IN ({','.join('?' for _ in news_ids)})
            """, news_ids)
        log.info(f"Marked {len(news_ids)} news articles as processed.")
        
    def close(self):
        self.conn.close()