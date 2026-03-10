import re
import os
import pandas as pd
from typing import Dict, List, Set, Optional

# Load mappings from CSV files in the data directory
_US_MAP = {}
_KR_MAP = {}

def load_mappings():
    global _US_MAP, _KR_MAP
    data_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")
    
    # Suffixes to remove for cleaner matching
    suffixes = [
        r'\s+inc\.?$', r'\s+corp\.?$', r'\s+ltd\.?$', r'\s+co\.?$', 
        r'\s+corporation$', r'\s+incorporated$', r'\s+plc\.?$', r'\s+llc\.?$',
        r'\s+group\.?$', r'\s+holdings?\.?$'
    ]
    suffix_pattern = re.compile('|'.join(suffixes), re.IGNORECASE)

    # Load US Tickers (NASDAQ/NYSE/etc from nasdaq_tickers.csv)
    us_csv = os.path.join(data_dir, "nasdaq_tickers.csv")
    if os.path.exists(us_csv):
        try:
            df = pd.read_csv(us_csv)
            for _, row in df.iterrows():
                symbol = str(row["Symbol"]).strip()
                original_name = str(row["Security Name"]).split("-")[0].split("(")[0].strip()
                # Normalize name: remove common suffixes
                name = suffix_pattern.sub('', original_name).strip()
                
                if len(name) > 3:
                    _US_MAP[name] = symbol
        except Exception as e:
            print(f"[Extractor] Error loading US CSV: {e}")

    # Load KR Tickers (KOSPI from kospi_tickers.csv)
    kr_csv = os.path.join(data_dir, "kospi_tickers.csv")
    if os.path.exists(kr_csv):
        try:
            df = pd.read_csv(kr_csv)
            for _, row in df.iterrows():
                code = str(row["종목코드"]).strip().zfill(6)
                name = str(row["종목명"]).strip()
                if len(name) > 1:
                    _KR_MAP[name] = code
        except Exception as e:
            print(f"[Extractor] Error loading KR CSV: {e}")

# Initial load
load_mappings()

def extract_tickers_from_news(news_item: dict) -> dict:
    """
    Unified ticker extraction for both KR and US markets.
    """
    tickers = set()
    needs_llm = False
    
    headline = news_item.get("headline") or news_item.get("title", "")
    if not headline:
        return {'tickers': set(), 'needs_llm': False}
    
    # Strategy 1: Regex for (TICKER)
    pattern_with_ticker = r'\(([A-Z0-9]{1,6})\)'
    found = re.findall(pattern_with_ticker, headline)
    tickers.update(found)
    
    # Strategy 2 & 3: CSV-based Name Matching with word boundaries
    # We use regex to ensure we don't match parts of words (e.g., "Apple" matching "Pineapple")
    headline_lower = headline.lower()
    
    for company_name, ticker in _US_MAP.items():
        # Escape for regex and check broad inclusion first for speed
        if company_name.lower() in headline_lower:
            # Verify with word boundaries
            pattern = rf'\b{re.escape(company_name)}\b'
            if re.search(pattern, headline, re.IGNORECASE):
                tickers.add(ticker)

    for company_name, ticker in _KR_MAP.items():
        if company_name in headline:
            tickers.add(ticker)
    
    # Identify if complex event needs LLM refinement (M&A, deals, etc.)
    # OR if we found nothing but the headline looks important
    keywords = ['acquisition', 'acquire', 'merger', 'buy', 'purchase', 'deal', 'partnership', 
                '주식', '급등', '공시', '상장', '체결', '실적', 'earnings']
    if not tickers or any(kw in headline.lower() for kw in keywords):
        # We always set needs_llm if we see a strategic keyword, even if we found a ticker,
        # to find OTHER related tickers or just for better context.
        if any(kw in headline.lower() for kw in keywords):
            needs_llm = True
    
    return {'tickers': tickers, 'needs_llm': needs_llm}

def extract_tickers_batch(news_list: List[dict]) -> List[str]:
    """
    Batch process news items to extract all relevant tickers.
    """
    all_tickers = set()
    for news in news_list:
        result = extract_tickers_from_news(news)
        all_tickers.update(result['tickers'])
    return sorted(list(all_tickers))
