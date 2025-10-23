import json
import re

from langchain.prompts import ChatPromptTemplate
from langchain.schema.output_parser import StrOutputParser

from ..utils import logger
from .stock import BaseAgent
from .. import prompts

log = logger.get_logger(__name__)


class NewsScreenerAgent(BaseAgent):
    """
    일반 시장 뉴스에서 분석할 가치가 있는 주식 티커를 동적으로 추출합니다.
    """

    def generate_watchlist_from_news(self, general_news: list) -> dict:
        if not general_news:
            return {"us": [], "kr": []}
        log.info("Running News Screener Agent to generate dynamic watchlist...")

        headlines = [f"- {news['headline']}" for news in general_news[:100]]

        prompt = ChatPromptTemplate.from_template(prompts.NEWS_SCREENER_PROMPT)
        chain = prompt | self.llm | StrOutputParser()
        response_str = ""
        try:
            # LLM을 호출하여 뉴스에서 티커 추출을 시도합니다.
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
            log.info(f"Dynamically generated watchlist: {watchlist}")
            return watchlist
        except Exception as e:
            log.error(
                f"Failed to parse tickers from news. Raw response: '{response_str}'. Error: {e}"
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
    ) -> dict:
        log.info("Running Market Condition Agent to determine dynamic thresholds...")
        headlines = [f"- {news['headline']}" for news in general_news[:20]]
        prompt = ChatPromptTemplate.from_template(prompts.MARKET_CONDITION_PROMPT)
        chain = prompt | self.llm | StrOutputParser()
        response_str = ""
        try:
            response_str = chain.invoke(
                {
                    "vix_value": vix_value,
                    "market_index_value": market_index_value,
                    "news_headlines": "\n".join(headlines),
                }
            )

            if not response_str or not response_str.strip():
                log.warning(
                    "Market Condition LLM returned an empty response. Falling back to default thresholds."
                )
                return {}  # 빈 딕셔너리를 반환하여 run_bot.py에서 기본값을 사용하도록 함

            if "```json" in response_str:
                response_str = response_str.split("```json")[1].split("```")[0].strip()
            result = json.loads(response_str)
            log.info(f"LLM Dynamic Market Condition Assessment: {result}")
            return result
        except Exception as e:
            log.error(
                f"Failed to determine dynamic market conditions with LLM. Raw response: '{response_str}'. Error: {e}"
            )
            return {}


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
        historical_analysis: list,  # ✨ 추가: 과거 분석 데이터
        relevant_news: list,  # ✨ 추가: 과거 관련 뉴스
        review_context: str,
    ) -> dict:
        log.info(f"Running deep portfolio review for {stock_code}...")

        prompt = ChatPromptTemplate.from_template(prompts.PORTFOLIO_REVIEW_PROMPT)
        chain = prompt | self.llm | StrOutputParser()
        response_str = ""
        try:
            response_str = chain.invoke(
                {
                    "stock_code": stock_code,
                    "initial_reasoning": initial_reasoning,
                    "current_analysis": json.dumps(current_analysis, default=str),
                    "historical_analysis": json.dumps(historical_analysis, default=str),
                    "relevant_news": json.dumps(relevant_news, default=str),
                    "review_context": review_context,
                }
            )

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
                f"Failed to review portfolio holding {stock_code}. Raw response: '{response_str}'. Error: {e}"
            )
            return {
                "conviction_score": 0,
                "recommendation_summary": f"Failed to parse LLM response: {e}",
            }


class EmergencyNewsAgent(BaseAgent):
    """
    개별 종목의 최신 뉴스를 분석하여, 즉시 청산해야 할 만큼 심각한 위기 상황인지를 판단합니다.
    """

    def analyze_news_for_emergency(self, stock_code: str, news_list: list) -> dict:
        """
        주어진 뉴스 목록을 분석하여 긴급 매도 필요 여부를 판단하고, 해당할 경우 그 이유를 반환합니다.
        """
        if not news_list:
            return {"is_emergency": False, "reason": "No news to analyze."}

        log.info(f"Running Emergency News Agent for {stock_code}...")
        headlines = [
            f"- Headline: {news['headline']}\n  Summary: {news['summary']}"
            for news in news_list[:10]
        ]  # 최근 뉴스 10개 분석

        prompt = ChatPromptTemplate.from_template(prompts.EMERGENCY_NEWS_PROMPT)
        chain = prompt | self.llm | StrOutputParser()
        response_str = ""
        try:
            response_str = chain.invoke(
                {"stock_code": stock_code, "news_headlines": "\n".join(headlines)}
            )
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
                f"Failed to analyze news for emergency on {stock_code}. Raw response: '{response_str}'. Error: {e}"
            )
            return {
                "is_emergency": False,
                "reason": f"Failed to parse LLM response: {e}",
            }