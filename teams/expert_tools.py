import json
import pandas as pd
import yfinance as yf
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
                    "max_investable_cash": {"type": "string", "description": "budget string e.g. '1000000 KRW'"}
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
    }
]

async def _fetch_ohlcv(code: str) -> pd.DataFrame:
    df = yf.download(code, period="3mo", auto_adjust=True, progress=False)
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
                res["current_price"] = float(df.iloc[-1]["Close"])
            return json.dumps(res)
        elif name == "analyze_news_sentiment":
            news = crawler.fetch_company_news(code)
            res = await sentiment_expert.analyze(news, vix_index=15.0)
            return json.dumps(res)
        elif name == "analyze_fundamentals":
            fund = crawler.fetch_company_fundamentals(code)
            res = await fundamental_expert.analyze(fund)
            return json.dumps(res)
        elif name == "analyze_qualitative_moat":
            prof = crawler.fetch_company_profile(code)
            news = crawler.fetch_company_news(code)
            res = await qualitative_expert.analyze(prof, news)
            return json.dumps(res)
        elif name == "analyze_chart_pattern":
            df = await _fetch_ohlcv(code)
            prof = crawler.fetch_company_profile(code)
            market_cap = prof.get("marketCapitalization")
            # Using defaults for 52w high/low if not fetched deeply
            res = await chart_expert.analyze(df, market_cap=market_cap, w52_high=0.0, w52_low=0.0)
            return json.dumps(res)
        elif name == "get_dynamic_watchlist":
            news = crawler.fetch_category_news("business")
            
            # Use original hybrid NewsScreener parsing
            base_tickers = await news_screener.generate_watchlist_from_news(news)
            
            # Apply ported US ticker extraction for maximum accuracy
            try:
                from core.us_ticker_extractor import extract_us_tickers_batch
                us_tickers = extract_us_tickers_batch(news)
                base_tickers.extend(us_tickers)
            except Exception as e:
                pass
                
            return json.dumps(list(set(base_tickers)))
        elif name == "finalize_batch_decision":
            res = await head_trader.finalize_allocations(
                critical_events="None",
                max_investable_cash=args.get("max_investable_cash", "0"),
                recent_fill_stats="None",
                past_insights="None",
                comprehensive_analyses=args.get("comprehensive_analyses")
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
        # Use default arg to capture tool_name in closure
        async def handler(n, a, _name=tool_name):
            return await handle_expert_tool(_name, a)
        agent.add_tool(tool, handler)
