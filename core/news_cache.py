import json
import os
from datetime import datetime, timedelta
import config
from core.monitor import get_system_logger

log = get_system_logger(__name__)

CACHE_FILE_PATH = os.path.join(config.DATA_DIR, "news_ticker_cache.json")
CACHE_TTL_HOURS = 6

class NewsTickerCache:
    """Manages ticker caching from off-market news analysis."""

    def __init__(self, cache_path: str = CACHE_FILE_PATH):
        self.cache_path = cache_path

    def load_cache(self) -> dict:
        if not os.path.exists(self.cache_path):
            return {"KR": {}, "US": {}}
        try:
            with open(self.cache_path, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception as e:
            log.error(f"Failed to load cache: {e}")
            return {"KR": {}, "US": {}}

    def save_cache(self, market_type: str, tickers: list, news_ids: list) -> bool:
        try:
            cache = self.load_cache()
            cache[market_type] = {
                "tickers": list(set(tickers)),
                "last_updated": datetime.now().isoformat(),
                "news_ids_processed": news_ids
            }
            with open(self.cache_path, 'w', encoding='utf-8') as f:
                json.dump(cache, f, indent=2, ensure_ascii=False)
            log.info(f"Saved {len(tickers)} {market_type} tickers to cache")
            return True
        except Exception as e:
            log.error(f"Failed to save cache: {e}")
            return False

    def is_cache_valid(self, market_type: str) -> bool:
        cache = self.load_cache()
        market_cache = cache.get(market_type, {})
        if not market_cache or "last_updated" not in market_cache:
            return False
        try:
            last_updated = datetime.fromisoformat(market_cache["last_updated"])
            return (datetime.now() - last_updated) < timedelta(hours=CACHE_TTL_HOURS)
        except Exception:
            return False

    def get_cached_tickers(self, market_type: str) -> list:
        if not self.is_cache_valid(market_type):
            return []
        cache = self.load_cache()
        return cache.get(market_type, {}).get("tickers", [])

    def merge_tickers(self, cached: list, fresh: list) -> list:
        return list(set(cached) | set(fresh))

    def clear_cache(self, market_type: str = None) -> bool:
        try:
            if market_type:
                cache = self.load_cache()
                cache[market_type] = {}
                with open(self.cache_path, 'w', encoding='utf-8') as f:
                    json.dump(cache, f, indent=2)
            else:
                with open(self.cache_path, 'w', encoding='utf-8') as f:
                    json.dump({"KR": {}, "US": {}}, f, indent=2)
            return True
        except Exception as e:
            log.error(f"Failed to clear cache: {e}")
            return False

news_cache = NewsTickerCache()
