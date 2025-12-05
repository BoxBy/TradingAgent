import json

import pandas as pd
import pandas_ta as ta
from langchain.prompts import ChatPromptTemplate
from langchain.schema.output_parser import StrOutputParser
from langchain_core.language_models.chat_models import BaseChatModel
from pydantic import BaseModel

from ..utils import logger
from .. import prompts

log = logger.get_logger(__name__)


class BaseAgent:
    """모든 LLM 기반 에이전트의 부모 클래스."""

    def __init__(self, llm: BaseChatModel):
        self.llm = llm


class TechnicalAnalysisAgent:
    """기술적 지표를 계산하고 요약하는 에이전트. LLM을 사용하지 않음."""

    def analyze(self, ohlcv_df: pd.DataFrame) -> dict:
        if ohlcv_df is None or ohlcv_df.empty:
            return {"error": "No OHLCV data available.", "summary": "No technical data"}

        log.info("Running Technical Analysis...")
        try:
            # 이동평균선, RSI 등 주요 기술 지표 계산
            ohlcv_df.ta.sma(length=20, append=True)
            ohlcv_df.ta.sma(length=60, append=True)
            ohlcv_df.ta.rsi(length=14, append=True)
            
            # ✨ 추가 지표: OBV, MFI, RVOL
            try:
                ohlcv_df.ta.obv(append=True)
                ohlcv_df.ta.mfi(length=14, append=True)
                # RVOL: Current Volume / SMA(Volume, 20)
                vol_sma = ohlcv_df["Volume"].rolling(window=20).mean()
                ohlcv_df["RVOL"] = ohlcv_df["Volume"] / vol_sma
            except Exception as e:
                log.warning(f"Failed to calculate advanced indicators: {e}")

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
            # 지표를 바탕으로 간단한 시그널 생성
            summary = []
            if all(
                k in analysis and analysis[k] is not None
                for k in ["current_price", "SMA_20", "SMA_60"]
            ):
                if analysis["current_price"] > analysis["SMA_20"] > analysis["SMA_60"]:
                    summary.append("Strong bullish trend (Golden Cross).")
                elif (
                    analysis["current_price"] < analysis["SMA_20"] < analysis["SMA_60"]
                ):
                    summary.append("Strong bearish trend (Dead Cross).")
            
            if "RVOL" in analysis and analysis["RVOL"] is not None:
                if analysis["RVOL"] > 2.0:
                    summary.append(f"Volume Spike detected (RVOL: {analysis['RVOL']:.1f}).")
            
            if "RSI_14" in analysis and analysis["RSI_14"] is not None:
                if analysis["RSI_14"] > 70:
                    summary.append("Overbought (RSI > 70).")
                elif analysis["RSI_14"] < 30:
                    summary.append("Oversold (RSI < 30).")
            
            if "MFI_14" in analysis and analysis["MFI_14"] is not None:
                if analysis["MFI_14"] > 80:
                    summary.append("Money Flow Overbought.")
                elif analysis["MFI_14"] < 20:
                    summary.append("Money Flow Oversold.")

            analysis["summary"] = (
                " ".join(summary) if summary else "No clear technical signal."
            )
            return analysis
        except Exception as e:
            log.error(f"Technical analysis failed: {e}")
            return {
                "error": f"Technical analysis failed: {e}",
                "summary": "Analysis failed",
            }


class SentimentAnalysisAgent(BaseAgent):
    """뉴스 헤드라인을 분석하여 감성 점수와 핵심 주제를 추출합니다."""

    def analyze(self, news_list: list, vix_index: float | None = None) -> dict:
        if not news_list:
            return {"error": "No news data available."}
        log.info(f"Running Sentiment Analysis Agent (VIX: {vix_index})...")
        headlines = [f"Headline: {news['headline']}" for news in news_list[:20]]
        prompt = ChatPromptTemplate.from_template(prompts.SENTIMENT_ANALYSIS_PROMPT)

        class SentimentResult(BaseModel):
            overall_sentiment: str
            sentiment_score: float | None = None
            sentiment_strength: str | None = None
            summary: str

        try:
            if hasattr(self.llm, "with_structured_output"):
                structured_llm = self.llm.with_structured_output(SentimentResult)
                chain = prompt | structured_llm
                result: SentimentResult = chain.invoke({
                    "news_headlines": "\n".join(headlines),
                    "vix_index": vix_index if vix_index is not None else "N/A"
                })
                if not result:
                    log.warning(
                        "Sentiment analysis LLM returned an empty structured response. Falling back to Neutral."
                    )
                    return {
                        "overall_sentiment": "Neutral",
                        "summary": "LLM structured response was empty.",
                    }
                return result.model_dump()

            chain = prompt | self.llm | StrOutputParser()
            response_str = chain.invoke({
                "news_headlines": "\n".join(headlines),
                "vix_index": vix_index if vix_index is not None else "N/A"
            })
            if not response_str or not response_str.strip():
                log.warning(
                    "Sentiment analysis LLM returned an empty response. Falling back to Neutral."
                )
                return {
                    "overall_sentiment": "Neutral",
                    "summary": "LLM response was empty.",
                }

            if "```json" in response_str:
                response_str = response_str.split("```json")[1].split("```")[0].strip()
            return json.loads(response_str)
        except Exception as e:
            log.error(
                f"Sentiment analysis failed. Error: {e}",
                exc_info=True,
            )
            return {
                "overall_sentiment": "Neutral",
                "summary": f"Failed to parse LLM response: {e}",
            }


class FundamentalAnalysisAgent(BaseAgent):
    """기업 프로필 데이터를 분석하여 재무 건전성과 가치를 평가합니다."""

    def analyze(self, fundamental_data: dict) -> dict:
        if not fundamental_data:
            return {"error": "No fundamental data available."}
        log.info("Running Fundamental Analysis Agent...")
        prompt = ChatPromptTemplate.from_template(prompts.FUNDAMENTAL_ANALYSIS_PROMPT)

        class FundamentalResult(BaseModel):
            has_red_flags: bool
            red_flag_summary: str

        try:
            payload = {"data": json.dumps(fundamental_data, indent=2)}
            if hasattr(self.llm, "with_structured_output"):
                structured_llm = self.llm.with_structured_output(FundamentalResult)
                chain = prompt | structured_llm
                result: FundamentalResult = chain.invoke(payload)
                if not result:
                    log.warning(
                        "Fundamental analysis LLM returned an empty structured response. Falling back to Unknown."
                    )
                    return {
                        "financial_health": "Unknown",
                        "valuation_summary": "LLM structured response was empty.",
                    }
                model = result.model_dump()
                return {
                    "has_red_flags": model.get("has_red_flags"),
                    "red_flag_summary": model.get("red_flag_summary", ""),
                }

            chain = prompt | self.llm | StrOutputParser()
            response_str = chain.invoke(payload)
            if not response_str or not response_str.strip():
                log.warning(
                    "Fundamental analysis LLM returned an empty response. Falling back to Unknown."
                )
                return {
                    "financial_health": "Unknown",
                    "valuation_summary": "LLM response was empty.",
                }

            if "```json" in response_str:
                response_str = response_str.split("```json")[1].split("```")[0].strip()
            return json.loads(response_str)
        except Exception as e:
            log.error(
                f"Fundamental analysis failed. Error: {e}",
                exc_info=True,
            )
            return {
                "financial_health": "Unknown",
                "valuation_summary": f"Failed to parse LLM response: {e}",
            }


class QualitativeAnalysisAgent(BaseAgent):
    """기업 프로필과 뉴스를 바탕으로 경쟁 우위, 경영진 등 정성적 요소를 평가합니다."""

    def analyze(self, company_profile: dict, company_news: list) -> dict:
        if not company_profile and not company_news:
            return {"error": "No qualitative data available."}
        log.info("Running Qualitative Analysis Agent...")
        news_headlines = [news["headline"] for news in company_news[:10]]
        prompt = ChatPromptTemplate.from_template(prompts.QUALITATIVE_ANALYSIS_PROMPT)

        class QualitativeResult(BaseModel):
            has_catalysts: bool
            catalyst_summary: str

        try:
            payload = {
                "profile": json.dumps(company_profile, indent=2),
                "news": "\n".join(news_headlines),
            }
            if hasattr(self.llm, "with_structured_output"):
                structured_llm = self.llm.with_structured_output(QualitativeResult)
                chain = prompt | structured_llm
                result: QualitativeResult = chain.invoke(payload)
                if not result:
                    log.warning(
                        "Qualitative analysis LLM returned an empty structured response. Falling back to Unknown."
                    )
                    return {
                        "competitive_moat": "Unknown",
                        "management_quality": "LLM structured response was empty.",
                    }
                model = result.model_dump()
                return {
                    "has_catalysts": model.get("has_catalysts"),
                    "catalyst_summary": model.get("catalyst_summary", ""),
                }

            chain = prompt | self.llm | StrOutputParser()
            response_str = chain.invoke(payload)
            if not response_str or not response_str.strip():
                log.warning(
                    "Qualitative analysis LLM returned an empty response. Falling back to Unknown."
                )
                return {
                    "competitive_moat": "Unknown",
                    "management_quality": "LLM response was empty.",
                }

            if "```json" in response_str:
                response_str = response_str.split("```json")[1].split("```")[0].strip()
            return json.loads(response_str)
        except Exception as e:
            log.error(
                f"Qualitative analysis failed. Error: {e}",
                exc_info=True,
            )
            return {
                "competitive_moat": "Unknown",
                "management_quality": f"Failed to parse LLM response: {e}",
            }


class ChartPatternAgent(BaseAgent):
    """과거 주가 데이터(OHLCV)를 보고 차트 패턴을 분석합니다."""

    def analyze_chart_patterns(
        self, 
        ohlcv_df: pd.DataFrame, 
        market_cap: float | None = None, 
        w52_high: float | None = None, 
        w52_low: float | None = None
    ) -> dict:
        if ohlcv_df is None or len(ohlcv_df) < 30:
            return {"summary": "Not enough chart data to analyze."}
        log.info("Running Chart Pattern Analysis Agent...")
        recent_data = ohlcv_df.tail(60).to_string()
        prompt = ChatPromptTemplate.from_template(prompts.CHART_PATTERN_PROMPT)
        chain = prompt | self.llm | StrOutputParser()
        try:
            summary = chain.invoke({
                "chart_data": recent_data,
                "market_cap": f"{market_cap:,.0f}" if market_cap else "N/A",
                "w52_high": f"{w52_high:,.2f}" if w52_high else "N/A",
                "w52_low": f"{w52_low:,.2f}" if w52_low else "N/A"
            })
            log.info(f"Chart Pattern Analysis: {summary}")
            return {"summary": summary}
        except Exception as e:
            log.error(f"Chart pattern analysis failed: {e}")
            raise e