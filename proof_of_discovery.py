import asyncio
import json
import os
import sys

# Add project root to path
sys.path.append("/home/ubuntu/TradingAgent")

from teams.expert_tools import handle_expert_tool
from data.crawler import MarketCrawler
from teams.experts import NewsScreenerExpert

async def proof_of_discovery():
    print("--- Proof of Discovery ---")
    crawler = MarketCrawler()
    screener = NewsScreenerExpert()
    
    print("1. Fetching general market news...")
    news = crawler.get_general_market_news("business")
    print(f"   Found {len(news)} news items.")
    
    print("\n2. Running NewsScreenerExpert...")
    try:
        # This calls generate_watchlist_from_news which uses the prompt and LLM
        # We'll use the handle_expert_tool wrapper to test the full fix
        res_json = await handle_expert_tool("get_dynamic_watchlist", {})
        tickers = json.loads(res_json)
        
        print("\n--- RESULTS ---")
        if tickers:
            print(f"✅ SUCCESSFULLY DISCOVERED: {tickers}")
        else:
            print("❌ No tickers discovered. Check if news content is sufficient.")
            
    except Exception as e:
        print(f"❌ FAILED: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    asyncio.run(proof_of_discovery())
