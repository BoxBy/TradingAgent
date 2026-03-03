"""
Stock Analysis Tools — ported from TradingAgent/src/agents/tools.py

Provides LLM-callable tools for:
1. search_additional_news: Fetch extra company-specific news mid-analysis
2. reassess_vix_threshold: Re-evaluate market risk using VIX + Fear & Greed
"""
import json
from datetime import datetime, timedelta
from data.crawler import MarketCrawler
from core.fear_greed import get_fear_greed_with_momentum

AGENT_TOOLS_SCHEMA = [
    {
        "type": "function",
        "function": {
            "name": "search_additional_news",
            "description": "Searches for additional company-specific news when more information is needed before making a final decision.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Search keywords for filtering relevant news."},
                    "stock_code": {"type": "string", "description": "The stock ticker code to search news for."}
                },
                "required": ["query", "stock_code"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "reassess_vix_threshold",
            "description": "Re-evaluates and sets a new dynamic VIX risk threshold based on current market conditions. Call ONLY when there is significant market-wide news or high uncertainty.",
            "parameters": {
                "type": "object",
                "properties": {},
                "required": []
            }
        }
    }
]

async def dispatch_agent_tool(tool_name: str, arguments: dict) -> str:
    crawler = MarketCrawler()
    
    if tool_name == "search_additional_news":
        query = arguments.get("query", "")
        stock_code = arguments.get("stock_code", "")
        try:
            news_list = crawler.fetch_category_news("business")
            filtered = [
                f"Headline: {n.get('title', '')}"
                for n in news_list
                if query.lower() in (n.get("title", "") + n.get("description", "")).lower()
            ]
            if not filtered:
                headlines = "\n".join([f"- {n.get('title', '')}" for n in news_list[:5]])
                return f"No news matching '{query}' for {stock_code}. Latest:\n{headlines}"
            return "Found relevant news:\n\n" + "\n\n".join(filtered[:10])
        except Exception as e:
            return f"Error searching news: {e}"
    
    elif tool_name == "reassess_vix_threshold":
        try:
            import yfinance as yf
            vix_data = yf.Ticker("^VIX").history(period="1d")
            current_vix = float(vix_data["Close"].iloc[-1]) if not vix_data.empty else 20.0
            fg = get_fear_greed_with_momentum(current_vix)
            return json.dumps({
                "current_vix": current_vix,
                "fear_greed_index": fg["index"],
                "level": fg["level"],
                "recommended_threshold": 30.0 if fg["level"] in ["Fear", "Extreme Fear"] else 35.0,
                "reasoning": f"Fear & Greed at {fg['index']} ({fg['level']}). VIX={current_vix:.1f}."
            })
        except Exception as e:
            return f"Error reassessing VIX: {e}"
    
    return f"Unknown agent tool: {tool_name}"

def register_agent_tools(agent):
    """Register search_additional_news and reassess_vix_threshold on a TradingAgentCore."""
    for schema in AGENT_TOOLS_SCHEMA:
        agent.add_tool(schema, dispatch_agent_tool)
