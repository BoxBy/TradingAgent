import sys
import os
import asyncio
import json

# Add current directory to path
sys.path.append(os.getcwd())

from core.market_ticker_extractor import extract_tickers_batch, _US_MAP, _KR_MAP, load_mappings
from data.crawler import MarketCrawler

async def investigate():
    print("--- 1. Mappings Check ---")
    load_mappings()
    print(f"US Map size: {len(_US_MAP)}")
    print(f"KR Map size: {len(_KR_MAP)}")
    
    # Test normalization theory
    test_names = ["Apple Inc.", "Microsoft Corp.", "Samsung Electronics"]
    print("\n--- 2. Hardcoded Matching Test ---")
    for name in test_names:
        found = False
        for k in _US_MAP.keys():
            if name in k or k in name:
                print(f"Match found for '{name}': Key='{k}', Ticker='{_US_MAP[k]}'")
                found = True
        if not found:
            print(f"No match found for '{name}' in US Map")

    print("\n--- 3. Live News Extraction Test ---")
    crawler = MarketCrawler()
    
    # Test Finnhub (General)
    print("\nFetching Finnhub 'general' news...")
    fh_news = crawler.get_general_market_news("general")
    print(f"Finnhub 'general' news count: {len(fh_news)}")
    if fh_news:
        tickers = extract_tickers_batch(fh_news)
        print(f"Headlines sample: {[n.get('headline', '')[:50] for n in fh_news[:3]]}")
        print(f"Extracted Tickers (Finnhub): {tickers}")
    
    # Test Naver (Fallback)
    print("\nFetching Naver '증시' news...")
    nv_news = crawler.get_news_from_naver("증시")
    print(f"Naver news count: {len(nv_news)}")
    if nv_news:
        tickers = extract_tickers_batch(nv_news)
        print(f"Headlines sample: {[n.get('title', '')[:50] for n in nv_news[:3]]}")
        print(f"Extracted Tickers (Naver): {tickers}")

if __name__ == "__main__":
    asyncio.run(investigate())
