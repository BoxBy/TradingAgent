import asyncio
import json
from datetime import datetime
from core.agent import TradingAgentCore
from data.crawler import MarketCrawler
from core.tools import dispatch_core_tool
from config import LLM_ENDPOINT, LLM_API_KEY, LLM_MODEL
from openai import AsyncOpenAI

class EmergencyAgent:
    """
    Independent monitor that runs in the background.
    Monitors VIX thresholds and individual stock breaking news to force emergency sell-offs.
    Replaces TradingAgent's EmergencyManager.
    """
    def __init__(self, crawler: MarketCrawler):
        self.crawler = crawler
        self.llm_agent = TradingAgentCore(system_prompt="You are a rapid emergency risk manager. Respond strictly in JSON.")
        # Use centralized LLM config
        self.llm_client = AsyncOpenAI(
            api_key=LLM_API_KEY,
            base_url=LLM_ENDPOINT
        )
        self.llm_model = LLM_MODEL

    async def check_vix_emergency(self, current_vix: float, threshold: float = 35.0):
        from core.fear_greed import get_fear_greed_with_momentum
        
        # Original simple threshold
        if current_vix > threshold:
            print(f"[EMERGENCY] VIX Spike Detected! Current: {current_vix}, Threshold: {threshold}")
            await self._liquidate_portfolio(reason=f"VIX exceeded threshold ({current_vix} > {threshold})")
            
        # Advanced CNN Fear & Greed check (Restored from TradingAgent)
        fg_data = get_fear_greed_with_momentum(current_vix)
        if fg_data['level'] == "Extreme Fear" and fg_data['index'] < 20:
            print(f"[EMERGENCY] Extreme Market Fear Detected! Index: {fg_data['index']}")
            await self._liquidate_portfolio(reason=f"CNN Fear & Greed Index hit {fg_data['index']} (Extreme Fear)")

    async def check_portfolio_news_emergency(self):
        """Monitors current portfolio for critical negative news. Improved with consolidated news and self.llm_agent."""
        try:
            # 1. Fetch current portfolio
            pf_str = await dispatch_core_tool("get_portfolio", {})
            if "API Error" in pf_str or "empty" in pf_str.lower():
                return
            
            # 2. Extract tickers using self.llm_agent for retry/token-limit support
            extract_prompt = f"Extract all stock tickers from this portfolio string as a JSON list: {pf_str}. Format: {{\"tickers\": [\"TICKER1\", \"TICKER2\"]}}"
            res = await self.llm_agent._call_llm_with_retry(
                messages=[{"role": "user", "content": extract_prompt}],
                response_format={"type": "json_object"}
            )
            
            if not res: return
            data = json.loads(res.choices[0].message.content)
            tickers = data.get("tickers", [])
            
            for ticker in tickers:
                # 3. Use consolidated news for high reliability
                news = self.crawler.get_consolidated_stock_news(ticker, limit=5)
                if not news: continue
                
                eval_prompt = f"""
                Analyze recent consolidated news for {ticker}:
                {json.dumps(news)}
                
                Is there an extreme negative catalyst (e.g., bankruptcy, major lawsuit, fraud, delisting)?
                Return JSON only: {{"is_emergency": boolean, "reason": "string"}}
                """
                
                eval_res = await self.llm_agent._call_llm_with_retry(
                    messages=[{"role": "user", "content": eval_prompt}],
                    response_format={"type": "json_object"}
                )
                
                if not eval_res: continue
                eval_data = json.loads(eval_res.choices[0].message.content)
                
                if eval_data.get("is_emergency"):
                     print(f"[EMERGENCY] Critical Risk for {ticker}: {eval_data.get('reason')}")
                     await self._liquidate_specific_position(ticker, reason=eval_data.get("reason", "Critical news"))

        except Exception as e:
            print(f"[EMERGENCY] Error checking portfolio news: {e}")

    async def _liquidate_portfolio(self, reason: str):
        print(f"!!! INITIATING FULL PORTFOLIO LIQUIDATION !!! Reason: {reason}")
        # Simplified execution call
        pass # In a complete implementation, iterate through pf and dispatch 'place_order' to sell all.

    async def _liquidate_specific_position(self, ticker: str, reason: str):
        print(f"!!! INITIATING SPECIFIC EMERGENCY LIQUIDATION for {ticker} !!! Reason: {reason}")
        from core.notification import send_notification
        send_notification(f"🚨 *긴급 매도 결정: {ticker}*\n━━━━━━━━━━━━━━━━━━━━\n💡 *이유*: {reason}")

    # ============================================================
    # EmergencyManagerSystem2 — LLM-based macro event impact analysis
    # Ported from TradingAgent/src/emergency/manager_llm.py
    # ============================================================

    async def analyze_macro_event_impact(self, event_description: str):
        """
        When a major macro event occurs (e.g., Fed rate hike, geopolitical crisis),
        use LLM to identify which portfolio stocks are affected and whether to sell.
        """
        # Step 1: Get current portfolio
        pf_str = dispatch_core_tool("get_portfolio", {})
        if "API Error" in pf_str or "empty" in pf_str.lower():
            print("[System2] Portfolio empty, nothing to analyze.")
            return

        # Step 2: Identify affected stocks
        identify_prompt = f"""
You are analyzing a major macroeconomic event and its impact on a portfolio.

EVENT: {event_description}

CURRENT PORTFOLIO:
{pf_str}

Identify which stocks in the portfolio are DIRECTLY affected by this event.
For each affected stock, state whether the impact is NEGATIVE (should sell) or POSITIVE (should hold/buy more).

Return JSON:
{{
  "affected_stocks": [
    {{"ticker": "AAPL", "impact": "NEGATIVE", "reasoning": "..."}},
    {{"ticker": "005930", "impact": "POSITIVE", "reasoning": "..."}}
  ],
  "unaffected_stocks": ["MSFT", "GOOGL"]
}}
"""
        try:
            res = await self.llm_client.chat.completions.create(
                model=self.llm_model,
                messages=[{"role": "user", "content": identify_prompt}],
                response_format={"type": "json_object"}
            )
            data = json.loads(res.choices[0].message.content)
            affected = data.get("affected_stocks", [])

            for stock in affected:
                ticker = stock.get("ticker")
                impact = stock.get("impact", "UNKNOWN")
                reasoning = stock.get("reasoning", "")

                if impact == "NEGATIVE":
                    # Step 3: Deep analysis before sell
                    confirm_prompt = f"""
A macro event may negatively affect {ticker}.
Event: {event_description}
Initial reasoning: {reasoning}

Current portfolio position data:
{pf_str}

Analyze deeper: Is this truly a SELL signal or should we HOLD despite the event?
Consider: Is the stock's fundamentals strong enough to weather this?

Return JSON:
{{"decision": "SELL" or "HOLD", "confidence": 0-100, "reasoning": "..."}}
"""
                    confirm_res = await self.llm_client.chat.completions.create(
                        model=self.llm_model,
                        messages=[{"role": "user", "content": confirm_prompt}],
                        response_format={"type": "json_object"}
                    )
                    confirm_data = json.loads(confirm_res.choices[0].message.content)

                    if confirm_data.get("decision") == "SELL" and confirm_data.get("confidence", 0) >= 70:
                        await self._liquidate_specific_position(
                            ticker,
                            reason=f"Macro Event Impact: {confirm_data.get('reasoning', reasoning)}"
                        )
                    else:
                        print(f"[System2] HOLD {ticker} despite event. Confidence: {confirm_data.get('confidence')}. Reason: {confirm_data.get('reasoning')}")

                elif impact == "POSITIVE":
                    print(f"[System2] {ticker} may BENEFIT from event: {reasoning}")

        except Exception as e:
            print(f"[System2] Error analyzing macro event: {e}")
