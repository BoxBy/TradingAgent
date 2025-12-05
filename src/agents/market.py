import json
import re

from langchain.prompts import ChatPromptTemplate
from langchain.schema.output_parser import StrOutputParser
from pydantic import BaseModel

from ..utils import logger
from .stock import BaseAgent
from .. import prompts

log = logger.get_logger(__name__)


class NewsScreenerAgent(BaseAgent):
    """
    일반 시장 뉴스에서 분석할 가치가 있는 주식 티커를 동적으로 추출합니다.
    """

    def generate_watchlist_from_news(self, general_news: list, market_type: str | None = None) -> dict:
        if not general_news:
            return {"us": [], "kr": []}
        log.info("Running News Screener Agent to generate dynamic watchlist...")

        headlines = [f"- {news['headline']}" for news in general_news[:100]]

        prompt = ChatPromptTemplate.from_template(prompts.NEWS_SCREENER_PROMPT)

        class NewsScreenerResult(BaseModel):
            us_tickers: list[str] = []
            kr_tickers: list[str] = []

        try:
            if hasattr(self.llm, "with_structured_output"):
                structured_llm = self.llm.with_structured_output(NewsScreenerResult)
                chain = prompt | structured_llm
                result: NewsScreenerResult = chain.invoke({"news_headlines": "\n".join(headlines)})
                if not result:
                    log.warning(
                        "News Screener LLM returned an empty structured response. Returning empty watchlist."
                    )
                    return {"us": [], "kr": []}
                model = result.model_dump()
                us_tickers = model.get("us_tickers", [])
                kr_tickers = model.get("kr_tickers", [])
                # ✨ Catalyst Date 추출 (아직은 로깅만)
                catalysts = model.get("catalysts", [])
                if catalysts:
                    log.info(f"Detected Catalysts: {catalysts}")
            else:
                chain = prompt | self.llm | StrOutputParser()
                response_str = chain.invoke({"news_headlines": "\n".join(headlines)})

                if not response_str or not response_str.strip():
                    log.warning(
                        "News Screener LLM returned an empty response. Returning empty watchlist."
                    )
                    return {"us": [], "kr": []}

                if "```json" in response_str:
                    response_str = response_str.split("```json")[1].split("```")[0].strip()

                result = json.loads(response_str)
                us_tickers = result.get("us_tickers", [])
                kr_tickers = result.get("kr_tickers", [])
                # ✨ Catalyst Date 추출 (아직은 로깅만)
                catalysts = result.get("catalysts", [])
                if catalysts:
                    log.info(f"Detected Catalysts: {catalysts}")

            # 정규표현식을 사용하여 유효한 형식의 티커/종목코드만 필터링합니다.
            valid_us = sorted(
                list(
                    set([t.upper() for t in us_tickers if re.match(r"^[A-Z]{1,5}$", t)])
                )
            )
            valid_kr = sorted(
                list(set([t for t in kr_tickers if re.match(r"^\d{6}$", t)]))
            )

            watchlist = {"us": valid_us, "kr": valid_kr}
            if market_type:
                mt = market_type.upper()
                if mt == "US":
                    filtered = {"us": valid_us, "kr": []}
                    log.info(f"Dynamically generated watchlist for US: {filtered['us']}")
                    return filtered
                elif mt == "KR":
                    filtered = {"us": [], "kr": valid_kr}
                    log.info(f"Dynamically generated watchlist for KR: {filtered['kr']}")
                    return filtered
            log.info(f"Dynamically generated watchlist: {watchlist}")
            return watchlist
        except Exception as e:
            log.error(
                f"Failed to parse tickers from news. Error: {e}",
                exc_info=True,
            )
            return {"us": [], "kr": []}


class MarketConditionAgent(BaseAgent):
    """
    VIX, 시장 지수, 뉴스를 종합하여 시장의 위험도와 투자 임계값을 동적으로 결정합니다.
    """

    def analyze(
        self,
        vix_value: float,
        market_index_value: float,
        general_news: list,
        cash_ratio: float | None = None,
        exposure_level: float | None = None,
        recent_fill_stats: dict | None = None,
        critical_events: str | None = None,
        rolling_base_equity_30d: float | None = None,
        pnl_30d_pct: float | None = None,
        max_drawdown_30d_pct: float | None = None,
        monthly_return_target_percent: float | None = None,
        monthly_drawdown_limit_percent: float | None = None,
        previous_reports: str | None = None,
    ) -> dict:
        log.info("Running Market Condition Agent to determine integrated thresholds...")
        headlines = [f"- {news['headline']}" for news in (general_news or [])[:20]]
        prompt = ChatPromptTemplate.from_template(prompts.MARKET_CONDITION_PROMPT)

        class MarketConditionResult(BaseModel):
            dynamic_vix_threshold: float
            buy_conviction_threshold: float
            sell_conviction_threshold: float
            confidence: float
            ttl_minutes: int
            reasoning: str
            top_sectors: list[str] = []  # ✨ 주도 섹터 추가

        try:
            payload = {
                "vix_value": float(vix_value) if vix_value is not None else 0.0,
                "market_index_value": float(market_index_value)
                if market_index_value is not None
                else 0.0,
                "news_headlines": "\n".join(headlines),
                "cash_ratio": float(cash_ratio) if cash_ratio is not None else 0.0,
                "exposure_level": float(exposure_level)
                if exposure_level is not None
                else 0.0,
                "recent_fill_stats": json.dumps(recent_fill_stats or {}, default=str),
                "critical_events": critical_events or "",
                "rolling_base_equity_30d": float(rolling_base_equity_30d)
                if rolling_base_equity_30d is not None
                else 0.0,
                "pnl_30d_pct": float(pnl_30d_pct) if pnl_30d_pct is not None else 0.0,
                "max_drawdown_30d_pct": float(max_drawdown_30d_pct)
                if max_drawdown_30d_pct is not None
                else 0.0,
                "monthly_return_target_percent": float(monthly_return_target_percent)
                if monthly_return_target_percent is not None
                else 20.0,
                "monthly_drawdown_limit_percent": float(monthly_drawdown_limit_percent)
                if monthly_drawdown_limit_percent is not None
                else 10.0,
                "previous_reports": previous_reports or "None",
            }

            if hasattr(self.llm, "with_structured_output"):
                structured_llm = self.llm.with_structured_output(MarketConditionResult)
                chain = prompt | structured_llm
                result_obj: MarketConditionResult = chain.invoke(payload)
                if not result_obj:
                    log.warning(
                        "Market Condition LLM returned an empty structured response. Falling back to default thresholds."
                    )
                    return {
                        "dynamic_vix_threshold": 35.0,
                        "buy_conviction_threshold": 6.0,
                        "sell_conviction_threshold": -6.0,
                        "confidence": 0.7,
                        "ttl_minutes": 90,
                        "reasoning": "empty_response",
                        "top_sectors": [],
                    }
                result = result_obj.model_dump()
            else:
                chain = prompt | self.llm | StrOutputParser()
                response_str = chain.invoke(payload)

                if not response_str or not response_str.strip():
                    log.warning(
                        "Market Condition LLM returned an empty response. Falling back to default thresholds."
                    )
                    return {
                        "dynamic_vix_threshold": 35.0,
                        "buy_conviction_threshold": 6.0,
                        "sell_conviction_threshold": -6.0,
                        "confidence": 0.7,
                        "ttl_minutes": 90,
                        "reasoning": "empty_response",
                        "top_sectors": [],
                    }

                if "```json" in response_str:
                    response_str = response_str.split("```json")[1].split("```")[0].strip()
                result = json.loads(response_str)
            
            # ✨ Log Top Sectors
            if result.get("top_sectors"):
                log.info(f"Identified Top Sectors: {result['top_sectors']}")

            # 숫자 필드 클램프 및 보수적 기본값 적용
            def _to_float(val, default):
                try:
                    return float(val)
                except Exception:
                    return default

            def _to_int(val, default):
                try:
                    return int(val)
                except Exception:
                    return default

            # PnL Bias Logic
            try:
                stats = json.loads(recent_fill_stats) if isinstance(recent_fill_stats, str) else (recent_fill_stats or {})
                avg_pnl = float(stats.get("avg_pnl_percent", 0.0))
                win_rate = float(stats.get("win_rate", 0.5))
                
                # Base thresholds
                buy_th = _to_float(result.get("buy_conviction_threshold", 6.0), 6.0)
                sell_th = _to_float(result.get("sell_conviction_threshold", -6.0), -6.0)

                # Apply Bias
                if avg_pnl > 0:
                    buy_th -= 0.3  # Aggressive: Lower threshold to buy more
                elif avg_pnl < 0:
                    buy_th += 0.3  # Conservative: Raise threshold to be picky
                
                if win_rate >= 0.6:
                    buy_th -= 0.2
                elif win_rate <= 0.4:
                    buy_th += 0.2
                
                # Clamp
                buy_th = max(-10.0, min(10.0, buy_th))
                sell_th = max(-10.0, min(10.0, sell_th))
                
                result["buy_conviction_threshold"] = buy_th
                result["sell_conviction_threshold"] = sell_th
                log.info(f"Applied PnL Bias: AvgPnL={avg_pnl}%, WinRate={win_rate} -> New Buy Threshold={buy_th}")

            except Exception as e:
                log.warning(f"Failed to apply PnL bias: {e}")
                # Fallback to LLM values if bias fails
                buy_th = _to_float(result.get("buy_conviction_threshold", 6.0), 6.0)
                buy_th = max(-10.0, min(10.0, buy_th))
                result["buy_conviction_threshold"] = buy_th

                sell_th = _to_float(result.get("sell_conviction_threshold", -6.0), -6.0)
                sell_th = max(-10.0, min(10.0, sell_th))
                result["sell_conviction_threshold"] = sell_th

            conf = _to_float(result.get("confidence", 0.7), 0.7)
            conf = max(0.0, min(1.0, conf))
            result["confidence"] = conf

            ttl = _to_int(result.get("ttl_minutes", 90), 90)
            ttl = max(30, min(240, ttl))
            result["ttl_minutes"] = ttl

            dyn_vix = result.get("dynamic_vix_threshold", 35.0)
            dyn_vix = _to_float(dyn_vix, 35.0)
            result["dynamic_vix_threshold"] = dyn_vix

            if not result.get("reasoning"):
                result["reasoning"] = "fallback_default"

            log.info(f"LLM Dynamic Market Condition Assessment: {result}")
            return result
        except Exception as e:
            log.error(
                f"Failed to determine dynamic market conditions with LLM. Error: {e}",
                exc_info=True,
            )
            return {
                "dynamic_vix_threshold": 35.0,
                "buy_conviction_threshold": 6.0,
                "sell_conviction_threshold": -6.0,
                "confidence": 0.7,
                "ttl_minutes": 90,
                "reasoning": "parse_error",
            }


class PortfolioReviewAgent(BaseAgent):
    """
    현재 보유 중인 종목에 대해, 초기 투자 논리를 비판적으로 재검토하고 보유/매도 의견을 제시합니다.
    ✨ RAG를 통해 과거 분석/뉴스 데이터를 함께 검토하고, 필요 시 거래 전략 수정을 제안합니다. ✨
    """

    def review_holding(
        self,
        stock_code: str,
        initial_reasoning: str,
        current_analysis: dict,
        historical_analysis: list,
        relevant_news: list,
        review_context: str,
        recent_fill_stats: dict,
        past_insights: list = [],  # ✨ 추가: 과거 매매 복기 데이터
    ) -> dict:
        log.info(f"Running deep portfolio review for {stock_code} (Context: {review_context})...")

        # Select Prompt based on Context
        if review_context == "PRE-PURCHASE VETTING":
            prompt_template = prompts.PRE_PURCHASE_VETTING_PROMPT
        else:
            prompt_template = prompts.PORTFOLIO_REVIEW_PROMPT
            
        prompt = ChatPromptTemplate.from_template(prompt_template)

        class PortfolioReviewResult(BaseModel):
            stock_code: str
            conviction_score: float
            recommendation_summary: str
            new_target_gain_percentage: float | None = None
            new_stop_loss_value: float | None = None

        try:
            payload = {
                "stock_code": stock_code,
                "initial_reasoning": initial_reasoning,
                "current_analysis": json.dumps(current_analysis, default=str),
                "historical_analysis": json.dumps(historical_analysis, default=str),
                "relevant_news": json.dumps(relevant_news, default=str),
                "review_context": review_context,
                "recent_fill_stats": json.dumps(recent_fill_stats, default=str),
                "past_insights": json.dumps(past_insights, default=str),  # ✨ 추가
            }

            if hasattr(self.llm, "with_structured_output"):
                structured_llm = self.llm.with_structured_output(PortfolioReviewResult)
                chain = prompt | structured_llm
                result_obj: PortfolioReviewResult = chain.invoke(payload)
                if not result_obj:
                    log.warning(
                        f"Portfolio review for {stock_code} returned an empty structured response."
                    )
                    return {
                        "conviction_score": 0,
                        "recommendation_summary": "LLM structured response was empty.",
                    }
                result = result_obj.model_dump()
            else:
                chain = prompt | self.llm | StrOutputParser()
                response_str = chain.invoke(payload)

                if not response_str or not response_str.strip():
                    log.warning(
                        f"Portfolio review for {stock_code} returned an empty response."
                    )
                    return {
                        "conviction_score": 0,
                        "recommendation_summary": "LLM response was empty.",
                    }

                if "```json" in response_str:
                    response_str = response_str.split("```json")[1].split("```")[0].strip()
                result = json.loads(response_str)

            result["stock_code"] = stock_code
            log.info(
                f"Portfolio Review for {stock_code}: Score {result.get('conviction_score')}"
            )
            return result
        except Exception as e:
            log.error(
                f"Failed to review portfolio holding {stock_code}. Error: {e}",
                exc_info=True,
            )
            return {
                "conviction_score": 0,
                "recommendation_summary": f"Failed to parse LLM response: {e}",
            }


class EmergencyNewsAgent(BaseAgent):
    """
    개별 종목의 최신 뉴스를 분석하여, 즉시 청산해야 할 만큼 심각한 위기 상황인지를 판단합니다.
    """

    def analyze_news_for_emergency(self, stock_code: str, news_list: list, price_change_percent: float | None = None) -> dict:
        """
        주어진 뉴스 목록을 분석하여 긴급 매도 필요 여부를 판단하고, 해당할 경우 그 이유를 반환합니다.
        """
        if not news_list:
            return {"is_emergency": False, "reason": "No news to analyze."}

        log.info(f"Running Emergency News Agent for {stock_code} (Price Change: {price_change_percent}%)...")
        headlines = [
            f"- Headline: {news['headline']}\n  Summary: {news['summary']}"
            for news in news_list[:10]
        ]  # 최근 뉴스 10개 분석

        prompt = ChatPromptTemplate.from_template(prompts.EMERGENCY_NEWS_PROMPT)

        class EmergencyNewsResult(BaseModel):
            is_emergency: bool
            reason: str

        try:
            payload = {
                "stock_code": stock_code, 
                "news_headlines": "\n".join(headlines),
                "price_change_percent": f"{price_change_percent:+.2f}%" if price_change_percent is not None else "N/A"
            }

            if hasattr(self.llm, "with_structured_output"):
                structured_llm = self.llm.with_structured_output(EmergencyNewsResult)
                chain = prompt | structured_llm
                result_obj: EmergencyNewsResult = chain.invoke(payload)
                if not result_obj:
                    log.warning(
                        f"Emergency News Agent for {stock_code} returned an empty structured response."
                    )
                    return {"is_emergency": False, "reason": "LLM structured response was empty."}
                result = result_obj.model_dump()
            else:
                chain = prompt | self.llm | StrOutputParser()
                response_str = chain.invoke(payload)
                if not response_str or not response_str.strip():
                    log.warning(
                        f"Emergency News Agent for {stock_code} returned an empty response."
                    )
                    return {"is_emergency": False, "reason": "LLM response was empty."}

                if "```json" in response_str:
                    response_str = response_str.split("```json")[1].split("```")[0].strip()

                result = json.loads(response_str)

            if result.get("is_emergency"):
                log.warning(
                    f"!!! EMERGENCY NEWS DETECTED for {stock_code}: {result.get('reason')} !!!"
                )
            return result
        except Exception as e:
            log.error(
                f"Failed to analyze news for emergency on {stock_code}. Error: {e}",
                exc_info=True,
            )
            return {
                "is_emergency": False,
                "reason": f"Failed to parse LLM response: {e}",
            }