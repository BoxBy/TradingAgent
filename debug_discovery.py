import asyncio
import json
from teams.expert_tools import handle_expert_tool
from data.crawler import MarketCrawler

async def main():
    print("Testing get_dynamic_watchlist tool live...")
    res = await handle_expert_tool("get_dynamic_watchlist", {})
    print(f"Result: {res}")
    
    crawler = MarketCrawler()
    news = crawler.get_general_market_news("business")
    print(f"Fetched {len(news)} headlines.")
    if news:
        print(f"First headline: {news[0].get('headline')}")
    
    from core.market_ticker_extractor import extract_tickers_batch
    tickers = extract_tickers_batch(news)
    print(f"Extracted Tickers: {tickers}")

if __name__ == "__main__":
    asyncio.run(main())
