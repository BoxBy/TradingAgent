import yfinance as yf
from typing import List

def get_comparative_vibe(tickers: List[str], period: str = "1mo") -> str:
    """
    Given a list of tickers, fetches market data and generates a comparative return summary.
    This acts as a 'Vibe Check' to quickly see which stock is outperforming.
    """
    if not tickers:
        return "No tickers provided for vibe check."

    vibe_report = f"--- Comparative Vibe Check (Period: {period}) ---\n"
    
    try:
        from core.ticker_utils import format_ticker_for_yfinance
        formatted_tickers = [format_ticker_for_yfinance(t) for t in tickers]
        # Download data for all tickers
        data = yf.download(formatted_tickers, period=period, group_by="ticker", auto_adjust=True, progress=False)
        
        # If only one ticker, yfinance doesn't group by ticker in the same way
        if len(tickers) == 1:
            t = formatted_tickers[0]
            original_t = tickers[0]
            if data.empty:
                return f"No data found for {t}."
            start_price = data['Close'].iloc[0].item()
            end_price = data['Close'].iloc[-1].item()
            ret = ((end_price - start_price) / start_price) * 100
            
            vibe_report += f"[{t}] Start: {start_price:.2f} -> End: {end_price:.2f} | Return: {ret:+.2f}%\n"
            vibe_report += f"Trend Vibe: {'🚀 BULLISH' if ret > 0 else '🩸 BEARISH'}\n"
            return vibe_report

        results = []
        for i, t in enumerate(formatted_tickers):
            df = data[t]
            original_t = tickers[i]
            if df.empty or df['Close'].dropna().empty:
                vibe_report += f"[{original_t}] No data available.\n"
                continue
                
            start_price = df['Close'].dropna().iloc[0].item()
            end_price = df['Close'].dropna().iloc[-1].item()
            ret = ((end_price - start_price) / start_price) * 100
            results.append({"ticker": original_t, "return": ret, "start": start_price, "end": end_price})
            
        # Sort by return descending
        results.sort(key=lambda x: x["return"], reverse=True)
        
        for r in results:
            trend = "🚀 OUTPERFORMER" if r['return'] > 5 else ("🩸 UNDERPERFORMER" if r['return'] < -5 else "⚖️ NEUTRAL")
            vibe_report += f"[{r['ticker']}] Return: {r['return']:+.2f}% | {trend} (Start: {r['start']:.2f}, End: {r['end']:.2f})\n"
            
        best = results[0]
        worst = results[-1]
        vibe_report += f"\n💡 Vibe Conclusion: {best['ticker']} dominates the cohort. {worst['ticker']} is lagging significantly."
        
    except Exception as e:
        vibe_report += f"\nError fetching vibe data: {str(e)}"
        
    return vibe_report

# Tool Schema for Teammates
VIBE_TOOLS_SCHEMA = [
    {
        "type": "function",
        "function": {
            "name": "CheckComparativeVibe",
            "description": "Compare the recent price performance (vibe) of multiple stock tickers. Returns a ranked summary.",
            "parameters": {
                "type": "object",
                "properties": {
                    "tickers": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "List of tickers (e.g., ['AAPL', 'MSFT', 'NVDA'])"
                    },
                    "period": {
                        "type": "string", 
                        "description": "Time period to compare (e.g., '1wk', '1mo', '3mo', '1y'). Default is '1mo'."
                    }
                },
                "required": ["tickers"]
            }
        }
    }
]

async def tool_check_comparative_vibe(tool_name: str, arguments: dict):
    tickers = arguments.get("tickers", [])
    period = arguments.get("period", "1mo")
    return get_comparative_vibe(tickers, period)

def register_vibe_tools(agent):
    agent.add_tool(VIBE_TOOLS_SCHEMA[0], tool_check_comparative_vibe)
