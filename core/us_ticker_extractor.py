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
    
    # Load US Tickers (NASDAQ/NYSE/etc from nasdaq_tickers.csv)
    us_csv = os.path.join(data_dir, "nasdaq_tickers.csv")
    if os.path.exists(us_csv):
        try:
            df = pd.read_csv(us_csv)
            # nasdaq_tickers.csv columns: Symbol,Security Name
            for _, row in df.iterrows():
                symbol = str(row["Symbol"]).strip()
                name = str(row["Security Name"]).split("-")[0].split("(")[0].strip()
                if len(name) > 3: # Ignore too short names to avoid false positives
                    _US_MAP[name] = symbol
        except Exception as e:
            print(f"[Extractor] Error loading US CSV: {e}")

    # Load KR Tickers (KOSPI from kospi_tickers.csv)
    kr_csv = os.path.join(data_dir, "kospi_tickers.csv")
    if os.path.exists(kr_csv):
        try:
            df = pd.read_csv(kr_csv)
            # kospi_tickers.csv columns: 종목코드,종목명
            for _, row in df.iterrows():
                code = str(row["종목코드"]).strip().zfill(6)
                name = str(row["종목명"]).strip()
                if len(name) > 1:
                    _KR_MAP[name] = code
        except Exception as e:
            print(f"[Extractor] Error loading KR CSV: {e}")

# Initial load
load_mappings()

def extract_us_tickers_from_news(news_item: dict) -> dict:
    tickers = set()
    needs_llm = False
    
    headline = news_item.get("headline") or news_item.get("title", "")
    if not headline:
        return {'tickers': set(), 'needs_llm': False}
    
    # Strategy 1: Regex for (TICKER)
    pattern_with_ticker = r'\(([A-Z]{1,5})\)'
    found = re.findall(pattern_with_ticker, headline)
    tickers.update(found)
    
    # Strategy 2: CSV-based Name Matching (US)
    for company_name, ticker in _US_MAP.items():
        if company_name in headline:
            tickers.add(ticker)

    # Strategy 3: CSV-based Name Matching (KR) - Add to results
    for company_name, ticker in _KR_MAP.items():
        if company_name in headline:
            tickers.add(ticker)
    
    # Identify if complex event needs LLM refinement
    if not tickers and any(kw in headline.lower() for kw in 
                          ['acquisition', 'acquire', 'merger', 'buy', 'purchase', 'deal', 'partnership']):
        needs_llm = True
    
    return {'tickers': tickers, 'needs_llm': needs_llm}

def extract_us_tickers_batch(news_list: List[dict]) -> List[str]:
    all_tickers = set()
    for news in news_list:
        result = extract_us_tickers_from_news(news)
        all_tickers.update(result['tickers'])
    return sorted(list(all_tickers))
