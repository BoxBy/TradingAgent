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
import config

class InternalExpert:
    """Base class for specialized TradingClaw sub-agents that return strict JSON."""
    def __init__(self, system_role: str = "You are a precise JSON-only financial analyzer."):
        self.agent = TradingAgentCore(system_prompt=system_role)

    def _get_strategy_vars(self):
        """Returns dynamic strategy variables for use in prompt formatting."""
        return {
            "target_profit_pct": config.USER_RULES.get("target_profit_percent_per_trade", 1.5),
            "stop_loss_pct": config.USER_RULES.get("max_loss_percent_per_trade", 7.0),
            "monthly_return_target_percent": config.USER_RULES.get("monthly_return_target_percent", 20.0),
            "max_holding_days": config.USER_RULES.get("max_holding_days", 3)
        }
        # Force JSON response type on the underlying client default args if needed, 
        # but usually Gemini on OpenAI compat respects JSON output natively if prompted.

    def _parse_json_text(self, raw_text: str) -> dict | None:
        """Attempt to parse JSON from raw LLM output, stripping markdown fences.
        Returns parsed dict on success, None on failure."""
        if raw_text is None or "Error" in raw_text:
            return None

        # Clean up any residual markdown fences
        clean_text = raw_text.strip()
        if clean_text.startswith("```json"):
            clean_text = clean_text[7:]
        elif clean_text.startswith("```"):
            clean_text = clean_text[3:]
        if clean_text.endswith("```"):
            clean_text = clean_text[:-3]

        clean_text = clean_text.strip()

        try:
            return json.loads(clean_text)
        except json.JSONDecodeError:
            # Fallback: try to find a JSON-like block with regex
            import re
            match = re.search(r'\{.*\}', clean_text, re.DOTALL)
            if match:
                try:
                    return json.loads(match.group(0))
                except json.JSONDecodeError:
                    return None
            return None

    async def _run_json_turn(self, prompt: str) -> dict:
        """Runs the LLM loop asynchronously with retry and graceful fallback.

        Strategy:
          1. Attempt 1: json_mode=True with full prompt.
          2. Attempt 2 (retry): json_mode=False with simplified messages (fresh context).
          3. If both fail, return a safe default dict instead of raising.
        """
        import logging
        logger = logging.getLogger(__name__)

        JSON_INSTRUCTION = "\n\nRETURN STRICT VALID JSON ONLY. NO MARKDOWN FENCES. NO EXPLANATIONS."
        full_prompt = prompt + JSON_INSTRUCTION
        safe_default = {"error": "API failed", "action": "HOLD"}

        # --- Attempt 1: json_mode=True (original behavior) ---
        try:
            self.agent.messages = [{"role": "system", "content": self.agent.system_prompt}]
            raw_text = await self.agent.run_turn(full_prompt, json_mode=True)
            result = self._parse_json_text(raw_text)
            if result is not None:
                return result
            logger.warning("Attempt 1 (json_mode=True): failed to parse JSON from response.")
        except Exception as exc:
            logger.warning("Attempt 1 (json_mode=True) raised: %s", exc)

        # --- Attempt 2: json_mode=False, simplified messages ---
        try:
            simplified_system = (
                "You are a precise JSON-only financial analyzer. "
                "Output ONLY valid JSON, no markdown, no commentary."
            )
            self.agent.messages = [{"role": "system", "content": simplified_system}]
            raw_text = await self.agent.run_turn(full_prompt, json_mode=False)
            result = self._parse_json_text(raw_text)
            if result is not None:
                return result
            logger.warning("Attempt 2 (json_mode=False, simplified): failed to parse JSON from response.")
        except Exception as exc:
            logger.warning("Attempt 2 (json_mode=False, simplified) raised: %s", exc)

        # --- All attempts exhausted: return safe default ---
        logger.error(
            "All _run_json_turn attempts failed for prompt (first 120 chars): %.120s — returning safe default.",
            prompt,
        )
        return safe_default


class MarketConditionExpert(InternalExpert):
    """Replaces TradingAgent's MarketConditionAgent."""
    async def analyze(self, vix_value: float, market_index_value: float, general_news: list, **kwargs) -> dict:
        headlines = [f"- {news.get('headline', '')}" for news in (general_news or [])[:20]]
        
        # Merge strategy defaults, explicit defaults, and runtime overrides cleanly
        context = self._get_strategy_vars()
        defaults = {
            "cash_ratio": 1.0,
            "exposure_level": 0.0,
            "recent_fill_stats": "None",
            "critical_events": "None",
            "rolling_base_equity_30d": 0,
            "pnl_30d_pct": 0,
            "max_drawdown_30d_pct": 0,
            "monthly_drawdown_limit_percent": 5.0,
            "previous_reports": "None"
        }
        for k, v in defaults.items():
            if k not in context:
                context[k] = v
                
        context.update(kwargs)
        
        prompt = MARKET_CONDITION_PROMPT.format(
            vix_value=vix_value,
            fear_greed_index="Neutral (50)", # Can inject real data later
            market_index_value=market_index_value,
            news_headlines="\n".join(headlines),
            **context
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
            past_insights=past_insights,
            **self._get_strategy_vars()
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
            recent_fill_stats=recent_fill_stats,
            **self._get_strategy_vars()
        )
        return await self._run_json_turn(prompt)

class HeadTraderExpert(InternalExpert):
    """Replaces TradingAgent's FINAL_BATCH_DECISION generator."""
    async def finalize_allocations(self, critical_events: str, **kwargs) -> dict:
        strategy_vars = self._get_strategy_vars()
        
        # Autonomous Override: Use hints from MarketConditionExpert if passed via orchestration
        if "recommended_target_profit_pct" in kwargs and kwargs["recommended_target_profit_pct"]:
            try:
                strategy_vars["target_profit_pct"] = float(kwargs["recommended_target_profit_pct"])
            except: pass
        if "recommended_stop_loss_pct" in kwargs and kwargs["recommended_stop_loss_pct"]:
            try:
                strategy_vars["stop_loss_pct"] = float(kwargs["recommended_stop_loss_pct"])
            except: pass

        prompt = FINAL_BATCH_DECISION_PROMPT.format(
            critical_events=critical_events,
            max_investable_cash=kwargs.get("max_investable_cash", "0 KRW"),
            recent_fill_stats=kwargs.get("recent_fill_stats", "None"),
            past_insights=kwargs.get("past_insights", "None"),
            comprehensive_analyses=kwargs.get("comprehensive_analyses", "{}"),
            monthly_return_target_percent=strategy_vars["monthly_return_target_percent"],
            **strategy_vars
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
        prompt = NEWS_SCREENER_PROMPT.format(
            news_headlines="\n".join(headlines),
            **self._get_strategy_vars()
        )
        return await self._run_json_turn(prompt)

class SentimentAnalysisExpert(InternalExpert):
    """Sentiment extraction."""
    async def analyze(self, news_list: list, vix_index: float) -> dict:
        headlines = [f"Headline: {news.get('headline', '')}" for news in (news_list or [])[:20]]
        prompt = SENTIMENT_ANALYSIS_PROMPT.format(
            news_headlines="\n".join(headlines),
            vix_index=vix_index,
            **self._get_strategy_vars()
        )
        return await self._run_json_turn(prompt)

class FundamentalAnalysisExpert(InternalExpert):
    """Fundamental red flag checking. Returns safe default for ETFs."""
    async def analyze(self, fundamental_data: dict) -> dict:
        # ETF: fundamental analysis is not applicable
        ticker = fundamental_data.get("symbol", fundamental_data.get("ticker", ""))
        from src.utils.asset_classifier import is_etf
        if is_etf(ticker):
            return {"health": "N/A (ETF)", "valuation": "N/A (ETF)", "summary": "ETF — fundamental analysis skipped, use technical/sentiment instead."}
        prompt = FUNDAMENTAL_ANALYSIS_PROMPT.format(
            data=json.dumps(fundamental_data, indent=2),
            **self._get_strategy_vars()
        )
        return await self._run_json_turn(prompt)

class QualitativeAnalysisExpert(InternalExpert):
    """Moat and Catalyst rating."""
    async def analyze(self, profile: dict, news: list) -> dict:
        news_headlines = [n.get("headline", "") for n in (news or [])[:10]]
        prompt = QUALITATIVE_ANALYSIS_PROMPT.format(
            profile=json.dumps(profile, indent=2),
            news="\n".join(news_headlines),
            **self._get_strategy_vars()
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
            w52_low=f"{w52_low:,.2f}" if w52_low else "N/A",
            **self._get_strategy_vars()
        )
        return await self._run_json_turn(prompt)
