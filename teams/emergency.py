import asyncio
import json
from datetime import datetime
from core.agent import TradingAgentCore
from data.crawler import MarketCrawler
from core.tools import dispatch_core_tool

class EmergencyAgent:
    """
    Independent monitor that runs in the background.
    Monitors VIX thresholds and individual stock breaking news to force emergency sell-offs.
    Replaces TradingAgent's EmergencyManager.
    """
    def __init__(self, crawler: MarketCrawler):
        self.crawler = crawler
        self.llm_agent = TradingAgentCore(system_prompt="You are a rapid emergency risk manager. Respond strictly in JSON.")

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
        """Monitors current portfolio for critical negative news."""
        pf_str = dispatch_core_tool("get_portfolio", {})
        if "API Error" in pf_str or "empty" in pf_str.lower():
            return
            
        try:
            # Assumes pf_str is parsable or we can extract tickers. 
            # In a real implementation we'd call the inner API. For now, doing string-based mock extract.
            # Using LLM to parse and decide
            prompt = f"""
            Here is the current portfolio raw string:
            {pf_str}

            Please extract all stock tickers currently held, and return them as a JSON list.
            Format: {{"tickers": ["AAPL", "005930"]}}
            """
            
            res = await self.llm_agent.client.chat.completions.create(
                model=self.llm_agent.model,
                messages=[{"role": "user", "content": prompt}],
                response_format={"type": "json_object"}
            )
            data = json.loads(res.choices[0].message.content)
            tickers = data.get("tickers", [])
            
            for ticker in tickers:
                news = self.crawler.fetch_category_news(category="business") # Fallback to general if specific fails
                # In real scenario we'd use get_company_news(ticker) - simulating here
                
                eval_prompt = f"""
                Analyze recent news for {ticker}:
                {json.dumps(news[:5])}
                
                Is there an extreme negative catalyst (e.g., bankruptcy, major lawsuit, fraud)?
                Return JSON: {{"is_emergency": boolean, "reason": "string"}}
                """
                
                eval_res = await self.llm_agent.client.chat.completions.create(
                    model=self.llm_agent.model,
                    messages=[{"role": "user", "content": eval_prompt}],
                    response_format={"type": "json_object"}
                )
                eval_data = json.loads(eval_res.choices[0].message.content)
                if eval_data.get("is_emergency"):
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
            res = await self.llm_agent.client.chat.completions.create(
                model=self.llm_agent.model,
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
                    confirm_res = await self.llm_agent.client.chat.completions.create(
                        model=self.llm_agent.model,
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
