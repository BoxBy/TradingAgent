import sys
import os
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from core.market_ticker_extractor import extract_tickers_batch

test_news = [
    {"headline": "Samsung Electronics labor union strike looms as negotiations fail."},
    {"headline": "SK hynix maintains AA+ credit rating despite market volatility."},
    {"headline": "NVDA stock reaches new highs as AI demand surges."},
    {"headline": "Microsoft (MSFT) partnership with OpenAI deepens."},
    {"headline": "Unknown company deal worth $1B (FAKE)."},
]

print("--- Testing Unified Ticker Extraction ---")
tickers = extract_tickers_batch(test_news)
print(f"Extracted Tickers: {tickers}")

expected = ["005930", "000660", "NVDA", "MSFT"]
all_found = all(t in tickers for t in expected)

if all_found:
    print("✅ SUCCESS: All expected tickers (KR & US) discovered.")
else:
    missing = [t for t in expected if t not in tickers]
    print(f"❌ FAILURE: Missing tickers: {missing}")

# Check needs_llm flag
from core.market_ticker_extractor import extract_tickers_from_news
res = extract_tickers_from_news({"headline": "Big tech merger announced between unnamed giants."})
print(f"Merge news needs LLM: {res['needs_llm']}")
