import json
import pandas as pd
import yfinance as yf
import os
from teams.experts import (
    TechnicalAnalysisAgent, MarketConditionExpert, PortfolioReviewExpert,
    PrePurchaseVettingExpert, HeadTraderExpert, NewsScreenerExpert,
    SentimentAnalysisExpert, FundamentalAnalysisExpert, QualitativeAnalysisExpert,
    ChartPatternExpert
)
from data.crawler import MarketCrawler
from core.agent import TradingAgentCore

# Instances
crawler = MarketCrawler()
tech_agent = TechnicalAnalysisAgent()
market_expert = MarketConditionExpert()
portfolio_expert = PortfolioReviewExpert()
vetting_expert = PrePurchaseVettingExpert()
head_trader = HeadTraderExpert()
news_screener = NewsScreenerExpert()
sentiment_expert = SentimentAnalysisExpert()
fundamental_expert = FundamentalAnalysisExpert()
qualitative_expert = QualitativeAnalysisExpert()
chart_expert = ChartPatternExpert()

EXPERT_TOOLS_SCHEMA = [
    {
        "type": "function",
        "function": {
            "name": "analyze_technical",
            "description": "Perform mathematical technical analysis (SMA, RSI, MFI, OBV) on a stock.",
            "parameters": {
                "type": "object",
                "properties": {"stock_code": {"type": "string"}},
                "required": ["stock_code"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "analyze_news_sentiment",
            "description": "Analyze recent news sentiment for a stock.",
            "parameters": {
                "type": "object",
                "properties": {"stock_code": {"type": "string"}},
                "required": ["stock_code"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "analyze_fundamentals",
            "description": "Evaluate company financial health.",
            "parameters": {
                "type": "object",
                "properties": {"stock_code": {"type": "string"}},
                "required": ["stock_code"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "analyze_qualitative_moat",
            "description": "Evaluate competitive moat and catalysts.",
            "parameters": {
                "type": "object",
                "properties": {"stock_code": {"type": "string"}},
                "required": ["stock_code"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "analyze_chart_pattern",
            "description": "Analyze OHLCV chart patterns like Head & Shoulders or Double Bottom.",
            "parameters": {
                "type": "object",
                "properties": {"stock_code": {"type": "string"}},
                "required": ["stock_code"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "get_dynamic_watchlist",
            "description": "Extract trending stock tickers from general news.",
            "parameters": {"type": "object", "properties": {}}
        }
    },
    {
        "type": "function",
        "function": {
            "name": "finalize_batch_decision",
            "description": "Invoke the HeadTrader LLM to compute final integer quantities based on max cash and conviction.",
            "parameters": {
                "type": "object",
                "properties": {
                    "comprehensive_analyses": {"type": "string", "description": "JSON string of all stock analysis data"},
                    "max_investable_cash": {"type": "string", "description": "budget string e.g. '1000000 KRW'"},
                    "recommended_target_profit_pct": {"type": "number", "description": "Optional cycle-level profit target override (e.g. 2.5)"},
                    "recommended_stop_loss_pct": {"type": "number", "description": "Optional cycle-level stop loss override (negative, e.g. -5.0)"}
                },
                "required": ["comprehensive_analyses", "max_investable_cash"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "pre_purchase_vetting",
            "description": "Invoke the PrePurchaseVetting LLM to do a final risk assessment of a proposed Buy decision.",
            "parameters": {
                "type": "object",
                "properties": {
                    "stock_code": {"type": "string"},
                    "initial_reasoning": {"type": "string"},
                    "current_analysis": {"type": "string"},
                    "historical_analysis": {"type": "string"},
                    "relevant_news": {"type": "string"},
                    "recent_fill_stats": {"type": "string"},
                    "past_insights": {"type": "string"}
                },
                "required": ["stock_code", "initial_reasoning", "current_analysis", "historical_analysis", "relevant_news", "recent_fill_stats", "past_insights"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "review_portfolio",
            "description": "Invoke the PortfolioReview LLM to assess an existing holding for profit-taking or exit.",
            "parameters": {
                "type": "object",
                "properties": {
                    "stock_code": {"type": "string"},
                    "initial_reasoning": {"type": "string"},
                    "current_analysis": {"type": "string"},
                    "historical_analysis": {"type": "string"},
                    "relevant_news": {"type": "string"},
                    "recent_fill_stats": {"type": "string"},
                    "past_insights": {"type": "string"}
                },
                "required": ["stock_code", "initial_reasoning", "current_analysis", "historical_analysis", "relevant_news", "recent_fill_stats", "past_insights"]
            }
        }
    }
]

# --- Tool Grouping for RBAC (Scoping) ---
TOOL_GROUPS = {
    "orchestrator": [
        "TaskCreate", "TaskList", "TaskGet",
        "get_buyable_cash", "get_portfolio",
        "read_file", "write_file", "edit_file"
    ],
    "news_analyst": [
        "get_dynamic_watchlist", "CheckComparativeVibe",
        "SearchMarketNews", "get_stock_news", "analyze_news_sentiment",
        "TaskUpdate", "TaskList"
    ],
    "tech_analyst": [
        "get_us_stock_price", "analyze_technical", "analyze_chart_pattern",
        "TaskUpdate", "TaskList", "read_file"
    ],
    "risk_trader": [
        "get_fundamental_data", "get_fundamental_ratios", "analyze_fundamentals", "analyze_qualitative_moat",
        "pre_purchase_vetting", "review_portfolio", "finalize_batch_decision", "place_order",
        "get_portfolio", "get_buyable_cash",
        "TaskUpdate", "TaskList", "read_file"
    ]
}

async def _fetch_ohlcv(code: str) -> pd.DataFrame:
    # Remove any non-alphanumeric characters like $ prefix
    import re
    ticker = re.sub(r'[^a-zA-Z0-9]', '', str(code))
    
    # Format ticker for yfinance
    from core.ticker_utils import format_ticker_for_yfinance
    formatted_ticker = format_ticker_for_yfinance(ticker)
        
    df = yf.download(formatted_ticker, period="3mo", auto_adjust=True, progress=False)
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.droplevel(1)
    return df

async def handle_expert_tool(name: str, args: dict) -> str:
    code = args.get("stock_code")
    try:
        if name == "analyze_technical":
            df = await _fetch_ohlcv(code)
            res = tech_agent.analyze(df)
            # Add current price so HeadTrader can use it to calculate qty
            if not df.empty:
                res["current_price"] = df.iloc[-1]["Close"].item()
            return json.dumps(res)
        elif name == "analyze_news_sentiment":
            news = crawler.get_consolidated_stock_news(code, limit=10)
            res = await sentiment_expert.analyze(news, vix_index=15.0)
            return json.dumps(res)
        elif name == "analyze_fundamentals":
            # ETF: skip fundamental analysis (not applicable)
            from src.utils.asset_classifier import is_etf
            if is_etf(code):
                return json.dumps({"health": "N/A (ETF)", "valuation": "N/A (ETF)", "summary": "ETF — fundamental analysis skipped."})
            fund = crawler.get_fundamental_data(code)
            res = await fundamental_expert.analyze(fund)
            return json.dumps(res)
        elif name == "analyze_qualitative_moat":
            prof = crawler.get_fundamental_data(code)
            news = crawler.get_consolidated_stock_news(code, limit=10)
            res = await qualitative_expert.analyze(prof, news)
            return json.dumps(res)
        elif name == "analyze_chart_pattern":
            df = await _fetch_ohlcv(code)
            prof = crawler.get_fundamental_data(code)
            market_cap = prof.get("marketCapitalization")
            # Using defaults for 52w high/low if not fetched deeply
            res = await chart_expert.analyze(df, market_cap=market_cap, w52_high=0.0, w52_low=0.0)
            return json.dumps(res)
        elif name == "get_dynamic_watchlist":
            news = crawler.get_general_market_news("general")
            
            # 1. LLM-based parsing (NewsScreenerExpert)
            screener_res = await news_screener.generate_watchlist_from_news(news)
            
            base_tickers = []
            if isinstance(screener_res, dict):
                base_tickers.extend(screener_res.get("us_tickers", []))
                base_tickers.extend(screener_res.get("kr_tickers", []))
            elif isinstance(screener_res, list):
                base_tickers = screener_res
            
            # 2. Hybrid supplementation with keyword extraction (Unified extraction)
            try:
                from core.market_ticker_extractor import extract_tickers_batch
                keyword_tickers = extract_tickers_batch(news)
                # Merge: LLM findings + Keyword findings (avoiding overwriting LLM IQ)
                result_tickers = list(set(base_tickers) | set(keyword_tickers))
            except Exception as e:
                print(f"[Warning] Unified ticker extraction failed: {e}")
                result_tickers = list(set(base_tickers))
            
            print(f"[Discovery] Found tickers: {result_tickers}")
            return json.dumps(result_tickers)
        elif name == "finalize_batch_decision":
            # Dynamic Feedback from MEMORY.md
            recent_fill_stats = "None"
            past_insights = "None"
            memory_file = os.path.join(os.getcwd(), "MEMORY.md")
            if os.path.exists(memory_file):
                try:
                    with open(memory_file, "r") as f:
                        content = f.read()
                        if "Recent Session Learnings" in content:
                            past_insights = content.split("## 💡 Recent Session Learnings")[-1].split("##")[0].strip()
                        if "Portfolio Focus & Allocation" in content:
                            recent_fill_stats = content.split("## 📊 Portfolio Focus & Allocation")[-1].split("##")[0].strip()
                except Exception as e:
                    print(f"[Error] Reading MEMORY.md for feedback: {e}")

            res = await head_trader.finalize_allocations(
                critical_events="None",
                max_investable_cash=args.get("max_investable_cash", "0"),
                recent_fill_stats=recent_fill_stats,
                past_insights=past_insights,
                comprehensive_analyses=args.get("comprehensive_analyses"),
                recommended_target_profit_pct=args.get("recommended_target_profit_pct"),
                recommended_stop_loss_pct=args.get("recommended_stop_loss_pct")
            )
            return json.dumps(res)
        elif name == "review_portfolio":
            res = await portfolio_expert.review_holding(
                stock_code=code,
                initial_reasoning=args.get("initial_reasoning", ""),
                current_analysis=args.get("current_analysis", ""),
                historical_analysis=args.get("historical_analysis", ""),
                relevant_news=args.get("relevant_news", ""),
                recent_fill_stats=args.get("recent_fill_stats", ""),
                past_insights=args.get("past_insights", "")
            )
            return json.dumps(res)
        elif name == "pre_purchase_vetting":
            res = await vetting_expert.review_holding(
                stock_code=code,
                initial_reasoning=args.get("initial_reasoning", ""),
                current_analysis=args.get("current_analysis", ""),
                historical_analysis=args.get("historical_analysis", ""),
                relevant_news=args.get("relevant_news", ""),
                recent_fill_stats=args.get("recent_fill_stats", ""),
                past_insights=args.get("past_insights", "")
            )
            return json.dumps(res)
        else:
            return f"Unknown expert tool: {name}"
    except Exception as e:
        return f"Error running {name}: {str(e)}"

def register_expert_tools(agent: TradingAgentCore):
    for tool in EXPERT_TOOLS_SCHEMA:
        tool_name = tool["function"]["name"]
        async def handler(n, a, _name=tool_name):
            return await handle_expert_tool(_name, a)
        agent.add_tool(tool, handler)

async def register_tools_by_group(agent: TradingAgentCore, group_name: str, mcp_bridge=None):
    """
    Surgically registers only the tools assigned to a specific role group.
    """
    if group_name not in TOOL_GROUPS:
        print(f"[Warning] Unknown tool group: {group_name}")
        return

    allowed_names = TOOL_GROUPS[group_name]
    print(f"[RBAC] Registering group '{group_name}' for agent. Allowed: {len(allowed_names)} tools.")

    # 1. Register from EXPERT_TOOLS_SCHEMA
    for tool in EXPERT_TOOLS_SCHEMA:
        t_name = tool["function"]["name"]
        if t_name in allowed_names:
            async def handler(n, a, _name=t_name):
                return await handle_expert_tool(_name, a)
            agent.add_tool(tool, handler)

    # 2. Register from TASK_TOOLS_SCHEMA (Imported here to avoid circulars)
    from teams.task_manager import TASK_TOOLS_SCHEMA, register_task_tools
    for tool in TASK_TOOLS_SCHEMA:
        if tool["function"]["name"] in allowed_names:
            from teams.task_manager import tool_task_create, tool_task_list, tool_task_get, tool_task_update
            h_map = {
                "TaskCreate": tool_task_create,
                "TaskList": tool_task_list,
                "TaskGet": tool_task_get,
                "TaskUpdate": tool_task_update
            }
            agent.add_tool(tool, h_map[tool["function"]["name"]])

    # 3. Register from CORE_TOOLS_SCHEMA (Standard FS/KIS tools)
    from core.tools import CORE_TOOLS_SCHEMA, dispatch_core_tool
    for tool in CORE_TOOLS_SCHEMA:
        if tool["function"]["name"] in allowed_names:
            agent.add_tool(tool, None) 

    # 4. Register RAG/Vibe tools if in group
    from data.rag import RAG_TOOLS_SCHEMA, tool_search_market_news
    if "SearchMarketNews" in allowed_names:
        agent.add_tool(RAG_TOOLS_SCHEMA[0], tool_search_market_news)
    
    from data.vibe_check import VIBE_TOOLS_SCHEMA, tool_check_comparative_vibe
    if "CheckComparativeVibe" in allowed_names:
        agent.add_tool(VIBE_TOOLS_SCHEMA[0], tool_check_comparative_vibe)

    # 5. MCP Tools (KIS Bridge) - Filtered Scoping
    if mcp_bridge:
        mcp_tools = await mcp_bridge.get_openai_tools()
        for tool in mcp_tools:
            t_name = tool["function"]["name"]
            if t_name in allowed_names:
                async def make_handler(tool_name):
                    async def handler(name, args):
                        return await mcp_bridge.call_tool(tool_name, args)
                    return handler
                
                h = await make_handler(t_name)
                agent.add_tool(tool, h)
                print(f"[RBAC] Registered MCP tool: {t_name}")
