import json
from typing import Dict, Any, List
from core.agent import TradingAgentCore
from teams.prompts import (
    MARKET_CONDITION_PROMPT,
    PORTFOLIO_REVIEW_PROMPT,
    NEWS_SCREENER_PROMPT,
    PRE_PURCHASE_VETTING_PROMPT,
    FINAL_BATCH_DECISION_PROMPT
)

class InternalExpert:
    """Base class for specialized TradingClaw sub-agents that return strict JSON."""
    def __init__(self, system_role: str = "You are a precise JSON-only financial analyzer."):
        self.agent = TradingAgentCore(system_prompt=system_role)
        # Force JSON response type on the underlying client default args if needed, 
        # but usually Gemini on OpenAI compat respects JSON output natively if prompted.

    async def _run_json_turn(self, prompt: str) -> dict:
        """Runs the LLM and forces a JSON parse of the output."""
        # Append instruction to guarantee raw JSON
        full_prompt = prompt + "\n\nRETURN STRICT VALID JSON ONLY. NO MARKDOWN FENCES. NO EXPLANATIONS."
        
        # We access the raw OpenAI client from the core agent
        response = await self.agent.client.chat.completions.create(
            model=self.agent.model,
            messages=[
                {"role": "system", "content": self.agent.system_prompt},
                {"role": "user", "content": full_prompt}
            ],
            response_format={"type": "json_object"}
        )
        
        raw_text = response.choices[0].message.content.strip()
        # Clean up any residual markdown fences if the model ignored the system prompt
        if raw_text.startswith("```json"):
            raw_text = raw_text[7:]
        if raw_text.startswith("```"):
            raw_text = raw_text[3:]
        if raw_text.endswith("```"):
            raw_text = raw_text[:-3]
            
        return json.loads(raw_text.strip())


class MarketConditionExpert(InternalExpert):
    """Replaces TradingAgent's MarketConditionAgent."""
    async def analyze(self, vix_value: float, market_index_value: float, general_news: list, **kwargs) -> dict:
        headlines = [f"- {news.get('headline', '')}" for news in (general_news or [])[:20]]
        prompt = MARKET_CONDITION_PROMPT.format(
            vix_value=vix_value,
            fear_greed_index="Neutral (50)", # Can inject real data later
            market_index_value=market_index_value,
            cash_ratio=kwargs.get("cash_ratio", 1.0),
            exposure_level=kwargs.get("exposure_level", 0.0),
            recent_fill_stats=kwargs.get("recent_fill_stats", "None"),
            critical_events=kwargs.get("critical_events", "None"),
            rolling_base_equity_30d=kwargs.get("rolling_base_equity_30d", 0),
            pnl_30d_pct=kwargs.get("pnl_30d_pct", 0),
            max_drawdown_30d_pct=kwargs.get("max_drawdown_30d_pct", 0),
            monthly_return_target_percent=kwargs.get("monthly_return_target_percent", 20.0),
            monthly_drawdown_limit_percent=kwargs.get("monthly_drawdown_limit_percent", 5.0),
            previous_reports=kwargs.get("previous_reports", "None"),
            news_headlines="\n".join(headlines)
        )
        return await self._run_json_turn(prompt)

class PortfolioReviewExpert(InternalExpert):
    """Replaces TradingAgent's PortfolioReviewAgent."""
    async def review_holding(self, stock_code: str, initial_reasoning: str, current_analysis: str, 
                             historical_analysis: str, relevant_news: str, recent_fill_stats: str, 
                             past_insights: str) -> dict:
        prompt = PORTFOLIO_REVIEW_PROMPT.format(
            stock_code=stock_code,
            initial_reasoning=initial_reasoning,
            current_analysis=current_analysis,
            historical_analysis=historical_analysis,
            relevant_news=relevant_news,
            recent_fill_stats=recent_fill_stats,
            past_insights=past_insights
        )
        return await self._run_json_turn(prompt)

class PrePurchaseVettingExpert(InternalExpert):
    """Replaces TradingAgent's PrePurchaseVetting logic."""
    async def review_holding(self, stock_code: str, initial_reasoning: str, current_analysis: str, 
                             historical_analysis: str, relevant_news: str, recent_fill_stats: str, 
                             past_insights: str) -> dict:
        prompt = PRE_PURCHASE_VETTING_PROMPT.format(
            stock_code=stock_code,
            initial_reasoning=initial_reasoning,
            current_analysis=current_analysis,
            historical_analysis=historical_analysis,
            relevant_news=relevant_news,
            recent_fill_stats=recent_fill_stats
        )
        return await self._run_json_turn(prompt)

class HeadTraderExpert(InternalExpert):
    """Replaces TradingAgent's FINAL_BATCH_DECISION generator."""
    async def finalize_allocations(self, critical_events: str, **kwargs) -> dict:
        prompt = FINAL_BATCH_DECISION_PROMPT.format(
            critical_events=critical_events,
            max_investable_cash=kwargs.get("max_investable_cash", "0 KRW"),
            recent_fill_stats=kwargs.get("recent_fill_stats", "None"),
            past_insights=kwargs.get("past_insights", "None"),
            comprehensive_analyses=kwargs.get("comprehensive_analyses", "{}")
        )
        return await self._run_json_turn(prompt)

import pandas as pd
import pandas_ta as ta

class TechnicalAnalysisAgent:
    """Restored mathematical Technical Analysis logic from TradingAgent."""
    def analyze(self, ohlcv_df: pd.DataFrame) -> dict:
        if ohlcv_df is None or ohlcv_df.empty:
            return {"error": "No OHLCV data available.", "summary": "No technical data"}

        try:
            import warnings
            # pandas_ta appends float results to int64-typed columns → FutureWarning
            # Work on a copy with reset index to avoid the dtype conflict
            df = ohlcv_df.copy().reset_index(drop=True)
            for col in ["Open", "High", "Low", "Close", "Volume"]:
                if col in df.columns:
                    df[col] = pd.to_numeric(df[col], errors='coerce').astype("float64")

            df.ta.sma(length=20, append=True)
            df.ta.sma(length=60, append=True)
            df.ta.rsi(length=14, append=True)

            with warnings.catch_warnings():
                warnings.simplefilter("ignore", FutureWarning)
                try:
                    df.ta.obv(append=True)
                    df.ta.mfi(length=14, append=True)
                    vol_sma = df["Volume"].rolling(window=20).mean()
                    df["RVOL"] = df["Volume"] / vol_sma
                except Exception:
                    pass

            ohlcv_df = df  # use the processed copy going forward

            latest_data = ohlcv_df.iloc[-1]
            analysis = {
                "SMA_20": latest_data.get("SMA_20"),
                "SMA_60": latest_data.get("SMA_60"),
                "RSI_14": latest_data.get("RSI_14"),
                "OBV": latest_data.get("OBV"),
                "MFI_14": latest_data.get("MFI_14"),
                "RVOL": latest_data.get("RVOL"),
                "current_price": latest_data["Close"],
            }
            summary = []
            if all(k in analysis and analysis[k] is not None for k in ["current_price", "SMA_20", "SMA_60"]):
                if analysis["current_price"] > analysis["SMA_20"] > analysis["SMA_60"]:
                    summary.append("Strong bullish trend (Golden Cross).")
                elif analysis["current_price"] < analysis["SMA_20"] < analysis["SMA_60"]:
                    summary.append("Strong bearish trend (Dead Cross).")
            
            if "RVOL" in analysis and analysis["RVOL"] is not None and analysis["RVOL"] > 2.0:
                summary.append(f"Volume Spike detected (RVOL: {analysis['RVOL']:.1f}).")
            
            if "RSI_14" in analysis and analysis["RSI_14"] is not None:
                if analysis["RSI_14"] > 70: summary.append("Overbought (RSI > 70).")
                elif analysis["RSI_14"] < 30: summary.append("Oversold (RSI < 30).")
            
            if "MFI_14" in analysis and analysis["MFI_14"] is not None:
                if analysis["MFI_14"] > 80: summary.append("Money Flow Overbought.")
                elif analysis["MFI_14"] < 20: summary.append("Money Flow Oversold.")

            analysis["summary"] = " ".join(summary) if summary else "No clear technical signal."
            return analysis
        except Exception as e:
            return {"error": f"Technical analysis failed: {e}", "summary": "Analysis failed"}

from teams.prompts import SENTIMENT_ANALYSIS_PROMPT, FUNDAMENTAL_ANALYSIS_PROMPT, QUALITATIVE_ANALYSIS_PROMPT, CHART_PATTERN_PROMPT

class NewsScreenerExpert(InternalExpert):
    """Dynamic ticker extraction from news."""
    async def generate_watchlist_from_news(self, general_news: list) -> dict:
        headlines = [f"- {news.get('headline', '')}" for news in (general_news or [])[:100]]
        prompt = NEWS_SCREENER_PROMPT.format(news_headlines="\n".join(headlines))
        return await self._run_json_turn(prompt)

class SentimentAnalysisExpert(InternalExpert):
    """Sentiment extraction."""
    async def analyze(self, news_list: list, vix_index: float) -> dict:
        headlines = [f"Headline: {news.get('headline', '')}" for news in (news_list or [])[:20]]
        prompt = SENTIMENT_ANALYSIS_PROMPT.format(
            news_headlines="\n".join(headlines),
            vix_index=vix_index
        )
        return await self._run_json_turn(prompt)

class FundamentalAnalysisExpert(InternalExpert):
    """Fundamental red flag checking."""
    async def analyze(self, fundamental_data: dict) -> dict:
        prompt = FUNDAMENTAL_ANALYSIS_PROMPT.format(data=json.dumps(fundamental_data, indent=2))
        return await self._run_json_turn(prompt)

class QualitativeAnalysisExpert(InternalExpert):
    """Moat and Catalyst rating."""
    async def analyze(self, profile: dict, news: list) -> dict:
        news_headlines = [n.get("headline", "") for n in (news or [])[:10]]
        prompt = QUALITATIVE_ANALYSIS_PROMPT.format(
            profile=json.dumps(profile, indent=2),
            news="\n".join(news_headlines)
        )
        return await self._run_json_turn(prompt)

class ChartPatternExpert(InternalExpert):
    """Pattern identification."""
    async def analyze(self, ohlcv_df: pd.DataFrame, market_cap: float = None, w52_high: float = None, w52_low: float = None) -> dict:
        if ohlcv_df is None or len(ohlcv_df) < 30:
            return {"pattern": "None", "implication": "None", "summary": "Not enough chart data."}
        recent_data = ohlcv_df.tail(60).to_string()
        prompt = CHART_PATTERN_PROMPT.format(
            chart_data=recent_data,
            market_cap=f"{market_cap:,.0f}" if market_cap else "N/A",
            w52_high=f"{w52_high:,.2f}" if w52_high else "N/A",
            w52_low=f"{w52_low:,.2f}" if w52_low else "N/A"
        )
        return await self._run_json_turn(prompt)
