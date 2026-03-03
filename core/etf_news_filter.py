from typing import List, Dict
from core.asset_classifier import get_etf_sector, get_sector_keywords, get_top_holdings
from core.monitor import get_system_logger

log = get_system_logger(__name__)

def filter_news_by_sector(all_news: List[Dict], sector: str, max_news: int = 20) -> List[Dict]:
    """섹터 키워드로 뉴스 필터링 (Layer 1)"""
    keywords = get_sector_keywords(sector)
    if not keywords:
        return all_news[:max_news]
    filtered = []
    for news in all_news:
        headline = news.get("headline", news.get("title", ""))
        summary = news.get("summary", news.get("description", ""))
        if any(kw in headline or kw in summary for kw in keywords):
            filtered.append(news)
            if len(filtered) >= max_news:
                break
    log.info(f"Filtered {len(filtered)} news for sector '{sector}'")
    return filtered

def get_holdings_tickers(ticker: str, etf_name: str) -> List[str]:
    return [h[0] for h in get_top_holdings(ticker, etf_name)]

def filter_news_by_holdings(all_news: List[Dict], holdings_tickers: List[str], max_news: int = 10) -> List[Dict]:
    """구성종목으로 뉴스 필터링 (Layer 2)"""
    if not holdings_tickers:
        return []
    filtered = []
    for news in all_news:
        headline = news.get("headline", news.get("title", ""))
        related = news.get("related", "")
        for ticker in holdings_tickers:
            if ticker in headline or ticker in related:
                filtered.append(news)
                break
        if len(filtered) >= max_news:
            break
    return filtered

def get_etf_news_hybrid(ticker: str, etf_name: str, all_news: List[Dict], sector_max: int = 15, holdings_max: int = 10) -> List[Dict]:
    """Hybrid 방식으로 ETF 관련 뉴스 수집."""
    sector = get_etf_sector(etf_name)
    sector_news = filter_news_by_sector(all_news, sector, max_news=sector_max)
    holdings_tickers = get_holdings_tickers(ticker, etf_name)
    holdings_news = filter_news_by_holdings(all_news, holdings_tickers, max_news=holdings_max)
    
    seen_urls = set()
    merged = []
    for news in sector_news + holdings_news:
        url = news.get("url", "")
        if url and url not in seen_urls:
            seen_urls.add(url)
            merged.append(news)
    log.info(f"ETF '{etf_name}' hybrid news: {len(sector_news)} sector + {len(holdings_news)} holdings = {len(merged)} unique")
    return merged
