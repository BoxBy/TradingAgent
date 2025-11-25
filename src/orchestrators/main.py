import json
from typing import Dict, List
from datetime import datetime, timedelta
import math
from pydantic import BaseModel

from ..agents.market import PortfolioReviewAgent
from .. import prompts

from langchain.agents import AgentExecutor, create_tool_calling_agent
from langchain.prompts import ChatPromptTemplate, MessagesPlaceholder
from langchain.schema.output_parser import StrOutputParser
# from langchain_core.language_models.chat_models import BaseChatModel # REMOVE THIS
from ..utils.api_key_manager import LLMProvider # IMPORT LLMProvider

from .. import config
from ..agents.stock import (
    ChartPatternAgent,
    FundamentalAnalysisAgent,
    QualitativeAnalysisAgent,
    SentimentAnalysisAgent,
    TechnicalAnalysisAgent,
)
from ..services import RAGManager
from ..utils import logger, ticker_utils

log = logger.get_logger(__name__)


class FinalDecisionItem(BaseModel):
    stock_code: str
    decision: str
    conviction_score: float
    quantity: int = 0
    stop_loss_percentage: float | None = None
    sell_deadline_date: str | None = None
    reasoning: str


class FinalDecisionPayload(BaseModel):
    decisions: List[FinalDecisionItem]


class TradingOrchestrator:
    """
    여러 분석 에이전트의 결과를 종합하고, 포트폴리오 최적화를 적용하여
    최종 매매 결정을 내리는 핵심 오케스트레이터.
    """

    def __init__(self, rag_manager: RAGManager, llm_provider: LLMProvider, tools: List):
        self.rag_manager = rag_manager
        self.llm_provider = llm_provider # Store llm_provider
        self.tools = tools
        self.technical_agent = TechnicalAnalysisAgent()
        self.sentiment_agent = SentimentAnalysisAgent(self.llm_provider) # Use get_llm()
        self.fundamental_agent = FundamentalAnalysisAgent(self.llm_provider) # Use get_llm()
        self.qualitative_agent = QualitativeAnalysisAgent(self.llm_provider) # Use get_llm()
        self.chart_pattern_agent = ChartPatternAgent(self.llm_provider) # Use get_llm()
        self.review_agent = PortfolioReviewAgent(self.llm_provider) # Use get_llm()

    def get_batch_trade_decisions(
        self, all_stocks_data: Dict[str, Dict], balance_info: Dict, market_conditions: Dict, max_investable_cash: float, market_type: str,
    ) -> Dict:
        """
        Watchlist의 모든 종목 데이터를 한번에 받아, 투자 결정 목록을 반환합니다.
        '매수 전 검토' 로직이 포함되어 있습니다.
        """
        log.info(f"--- Starting Batch Analysis for {list(all_stocks_data.keys())} ---")

        comprehensive_analyses = {}
        for stock_code, data_bundle in all_stocks_data.items():
            recent_review_history = self.rag_manager.retrieve_relevant_analysis(
                stock_code, "Recent portfolio review decisions for this stock.", n_results=3
            )
            comprehensive_analyses[stock_code] = {
                "current_price": data_bundle.get("current_price", "None"),
                "technical_indicator_analysis": self.technical_agent.analyze(data_bundle.get("ohlcv")),
                "chart_pattern_analysis": self.chart_pattern_agent.analyze_chart_patterns(data_bundle.get("ohlcv")),
                "sentiment_analysis": self.sentiment_agent.analyze(data_bundle.get("news")),
                "fundamental_analysis": self.fundamental_agent.analyze(data_bundle.get("fundamentals")),
                "qualitative_analysis": self.qualitative_agent.analyze(data_bundle.get("fundamentals"), data_bundle.get("news")),
                "recent_review_history": recent_review_history,
            }

        watchlist = list(all_stocks_data.keys())
        critical_events = self.rag_manager.retrieve_recent_critical_events(watchlist, days=7)

        # 3-1단계: '공격적인 매수 담당자'가 초기 제안을 생성합니다.
        initial_payload = self._make_final_batch_decision(
            comprehensive_analyses, balance_info, critical_events, max_investable_cash, market_type
        )
        initial_decisions = initial_payload.get("decisions", [])
        
        log.info(f"initial decisions : {initial_payload}")
        
        final_vetted_decisions = []
        
        # 1. 파라미터 정의
        VIX_MIDPOINT = 25.0  # V0
        VIX_STEEPNESS = 0.2    # k
        MIN_REVIEWER_WEIGHT = 0.25 # W_min
        MAX_REVIEWER_WEIGHT = 0.75 # W_max

        vix_index = market_conditions.get("vix_index", 15)
        log.info(f"Current VIX Index: {vix_index:.2f}. Calculating weights with sigmoid function.")
        
        # 2. 시그모이드 함수를 이용한 가중치 계산
        weight_range = MAX_REVIEWER_WEIGHT - MIN_REVIEWER_WEIGHT
        sigmoid_value = 1 / (1 + math.exp(-VIX_STEEPNESS * (vix_index - VIX_MIDPOINT)))
        
        REVIEWER_WEIGHT = MIN_REVIEWER_WEIGHT + (weight_range * sigmoid_value)
        BUYER_WEIGHT = 1.0 - REVIEWER_WEIGHT

        log.info(f"Dynamic Weights -> Buyer: {BUYER_WEIGHT:.2%}, Reviewer: {REVIEWER_WEIGHT:.2%}")
        

        for decision in initial_decisions:
            stock_code = decision.get("stock_code")
            
            # 3-2단계: 'BUY' 결정에 대해서만 '리스크 분석가'의 검토(1차 방어선)를 받습니다.
            if decision.get("decision") == "BUY" and stock_code in all_stocks_data:
                log.info(f"Performing pre-purchase risk review for {stock_code}...")
                
                buyer_score = decision.get("conviction_score", 0)
                
                current_analysis_for_review = all_stocks_data[stock_code]
                # historical_analysis = self.rag_manager.retrieve_relevant_analysis(stock_code, decision.get("reasoning", ""), n_results=3)
                relevant_news = self.rag_manager.retrieve_relevant_news(stock_code, decision.get("reasoning", ""), n_results=8)
                historical_analysis = relevant_news[:3]

                # 리스크 검토 에이전트 호출 (Context: PRE-PURCHASE VETTING)
                try:
                    review_result = self.review_agent.review_holding(
                        stock_code=stock_code,
                        initial_reasoning=decision.get("reasoning", ""),
                        current_analysis=current_analysis_for_review,
                        historical_analysis=historical_analysis,
                        relevant_news=relevant_news,
                        review_context="PRE-PURCHASE VETTING"
                    )
                except:
                    review_result = {}
                
                # reviewer_score = min(review_result.get("conviction_score", 0) if review_result else 0, 0)
                reviewer_score = review_result.get("conviction_score", -3) if review_result else -3
                
                # 'Veto' 로직을 '가중치 합산' 로직으로 교체
                final_score = (buyer_score * BUYER_WEIGHT) + (reviewer_score * REVIEWER_WEIGHT)
                log.info(f"Scoring for {stock_code}: Buyer({buyer_score:.1f}) * {BUYER_WEIGHT:.2f} + Reviewer({reviewer_score:.1f}) * {REVIEWER_WEIGHT:.2f} = Final({final_score:.2f})")
                
                decision["conviction_score"] = final_score # 최종 점수로 확신 점수 업데이트
            # BUY가 아닌 결정(HOLD, SELL)은 그대로 통과시킵니다.
            final_vetted_decisions.append(decision)
        
        # 최종 승인된 결정(final_vetted_decisions)을 final_decisions 변수에 할당합니다.
        
        # 1. 최종 페이로드를 initial_payload를 기반으로 생성합니다.
        final_payload = initial_payload.copy()
        
        # 2. 페이로드의 'decisions' 키를 검증 완료된 리스트로 교체합니다.
        final_payload["decisions"] = final_vetted_decisions

        # 3. 이후 로직에서는 final_payload를 사용합니다.
        final_decisions = final_payload.get("decisions", [])

        buy_decisions = [d for d in final_decisions if d.get("decision") == "BUY"]

        # 예산 분배 로직은 최종 승인된 매수 건에 대해서만 실행됩니다.
        if buy_decisions:
            log.info("Allocating budget for BUY decisions...")
            try:
                # 이번 배치에서 사용할 총 투자금을 계산합니다.
                total_investment_amount = max_investable_cash
                log.info(f"Final investment amount for this batch: {total_investment_amount:,.0f} KRW or USD")

                # 소프트맥스 기반 가중치로 총 투자금을 종목별로 분배합니다.
                tau = 2.0
                exps = []
                for d in buy_decisions:
                    exps.append(math.exp((d.get("conviction_score", 5.0)) / tau))
                denom = sum(exps) or 1.0

                for idx, decision in enumerate(buy_decisions):
                    stock_code = decision["stock_code"]
                    price_data = all_stocks_data[stock_code]["ohlcv"]
                    if price_data is not None and not price_data.empty:
                        stock_price = price_data.iloc[-1]["Close"]
                        weight = exps[idx] / denom
                        investment_per_stock = total_investment_amount * weight
                        
                        # config의 종목당 최대 투자 한도를 여전히 존중
                        investment_per_stock = min(investment_per_stock, config.USER_RULES.get("max_investment_per_stock", 100000000))

                        # 최종 수량 계산 (기존 quantity는 무시하고 덮어쓰기)
                        decision["quantity"] = int(investment_per_stock / stock_price) if stock_price > 0 else 0
                        log.info(f"Allocated for {stock_code}: {investment_per_stock:,.0f} KRW -> {decision['quantity']} shares.")

            except Exception as e:
                log.error(f"Portfolio allocation failed: {e}. No trades will be executed.")
                # 실패 시 매수 결정 수량을 모두 0으로 만들어 매수를 막음
                for decision in buy_decisions:
                    decision["quantity"] = 0

        
        analyses_to_add = []
        for decision in final_decisions:
            stock_code = decision.get("stock_code")
            if stock_code in comprehensive_analyses:
                analyses_to_add.append({"stock_code": stock_code, "report": json.dumps(comprehensive_analyses[stock_code], default=str)})
        
        if analyses_to_add:
            self.rag_manager.add_analyses_to_db(analyses_to_add)

        log.info(f'final_decisions : {final_decisions}')
        return final_payload

    def _make_final_batch_decision(self, analyses: Dict, balance: Dict, critical_events: str, max_investable_cash: float, market_type: str, ) -> Dict:
        """여러 종목의 분석 결과를 종합하여 최종 투자 계획과 예산 사용률을 반환하는 LLM 에이전트."""
        log.info("Running Final Batch Decision Agent...")

        relevant_insights = []
        for stock_code in analyses.keys():
            query = json.dumps(analyses[stock_code].get("sentiment_analysis", {}))
            insights = self.rag_manager.retrieve_relevant_insights(stock_code, query)
            if insights:
                relevant_insights.extend(insights)

        def _summarize_insights(texts: List[str]) -> str:
            if not texts:
                return "No past insights available."
            joined = "\n".join(texts[:10])
            prompt = ChatPromptTemplate.from_template(
                "Summarize the following trading insights into at most 6 concise bullet lines in plain text without markdown or numbering. Keep under 600 characters.\n\nInsights:\n{insights}\n\nSummary:" )
            chain = prompt | self.llm_provider | StrOutputParser()
            try:
                return chain.invoke({"insights": joined})[:600]
            except Exception:
                return "\n".join(texts[:6])[:600]

        past_insights_text = _summarize_insights(relevant_insights)
        recent_fill_stats_text = json.dumps(self.rag_manager.get_recent_fill_stats(days=3), default=str)

        rolling_base_equity_30d = balance.get("rolling_30d_base_equity")
        rolling_pnl_30d_pct = balance.get("rolling_30d_pnl_percent")
        rolling_dd_30d_pct = balance.get("rolling_30d_max_drawdown_percent")
        monthly_target_pct = balance.get("monthly_return_target_percent")
        monthly_dd_limit_pct = balance.get("monthly_drawdown_limit_percent")

        prompt = ChatPromptTemplate.from_messages([
            ("system", prompts.FINAL_BATCH_DECISION_PROMPT),
            ("human", 
             "Today's Date: {current_date} (Current Time: {current_time} KST)\n\n"
             "Analyze the following stocks and provide your trading decisions.\n"
             "Maximum Investable Cash for this Batch: {max_investable_cash:,.0f} {currency}\n"
             "Account Status: {account_status}\n"
             "User Investment Rules (currently set to '{trading_style}' style): {user_rules}\n"
             "Rolling 30-day Performance: base=₩{rolling_base_equity_30d:,.0f}, pnl={rolling_pnl_30d_pct:+.2f}%, max_dd={rolling_dd_30d_pct:+.2f}%, target={monthly_target_pct:.2f}%, dd_limit={monthly_dd_limit_pct:.2f}%\n"
             "Recent Fill Stats: {recent_fill_stats}\n"
             "**Past Trading Insights (Learn from these!):**\n{past_insights}\n"
             "Comprehensive Analyses for all stocks (including general review history):\n{comprehensive_analyses}"),
            MessagesPlaceholder(variable_name="agent_scratchpad"),
        ])

        agent = create_tool_calling_agent(self.llm_provider, self.tools, prompt) # Use get_llm()
        agent_executor = AgentExecutor(
            agent=agent, tools=self.tools, verbose=True, handle_parsing_errors=True
        )

        output_str = ""
        try:
            response = agent_executor.invoke({
                "current_date": datetime.now().strftime('%Y-%m-%d'),
                "current_time": datetime.now().strftime('%H:%M:%S'),
                "max_investable_cash": max_investable_cash, # ✨ LLM에게 정보 제공
                "currency" : "KRW" if market_type == "KR" else "USD",
                "account_status": json.dumps(balance),
                "trading_style": config.TRADING_STYLE,
                "user_rules": json.dumps(config.USER_RULES),
                "past_insights": past_insights_text,
                "recent_fill_stats": recent_fill_stats_text,
                "critical_events": critical_events,
                "comprehensive_analyses": json.dumps(analyses, default=str),
                "rolling_base_equity_30d": rolling_base_equity_30d or 0.0,
                "rolling_pnl_30d_pct": rolling_pnl_30d_pct or 0.0,
                "rolling_dd_30d_pct": rolling_dd_30d_pct or 0.0,
                "monthly_target_pct": monthly_target_pct or 0.0,
                "monthly_dd_limit_pct": monthly_dd_limit_pct or 0.0,
            })
            output_str = response.get("output", "{}")

            if not output_str or not output_str.strip():
                log.warning(
                    "Final batch decision agent returned an empty response. Returning empty payload."
                )
                return {}

            if "```json" in output_str:
                output_str = output_str.split("```json")[1].split("```")[0].strip()

            result = json.loads(output_str)

            # Normalize reasoning key: if LLM responded with 'reason' instead of 'reasoning',
            # copy it over so downstream code always sees 'reasoning'.
            try:
                for d in result.get("decisions", []):
                    if isinstance(d, dict) and "reasoning" not in d and "reason" in d:
                        d["reasoning"] = d["reason"]
            except Exception:
                pass

            # Pydantic 스키마를 사용해 decisions 구조와 타입을 검증합니다.
            # 필수 필드 누락이나 타입 오류가 있으면 이번 배치는 비워서 반환합니다.
            try:
                validated = FinalDecisionPayload.parse_obj(result)
                result = validated.dict()
            except Exception as e:
                log.error(
                    f"Final batch decision payload failed schema validation. Raw result: {result}. Error: {e}",
                    exc_info=True,
                )
                return {}

            try:
                today = datetime.now().date()
                max_deadline = today + timedelta(days=14)
                for d in result.get("decisions", []):
                    if d.get("decision") == "BUY":
                        if isinstance(d.get("quantity"), (int, float)):
                            d["quantity"] = int(max(0, d.get("quantity", 0)))
                        if isinstance(d.get("stop_loss_percentage"), (int, float)) and d["stop_loss_percentage"] >= 0:
                            d["stop_loss_percentage"] = abs(d["stop_loss_percentage"])  # normalize to positive percent
                        try:
                            deadline = datetime.strptime(d.get("sell_deadline_date", ""), "%Y-%m-%d").date()
                            if deadline > max_deadline:
                                d["sell_deadline_date"] = max_deadline.strftime("%Y-%m-%d")
                            if deadline < today:
                                d["sell_deadline_date"] = max(today, min(max_deadline, today + timedelta(days=3))).strftime("%Y-%m-%d")
                        except Exception:
                            d["sell_deadline_date"] = max_deadline.strftime("%Y-%m-%d")
            except Exception:
                pass

            for d in result.get("decisions", []):
                if not d.get("reasoning"):
                    d["reasoning"] = "No reasoning provided by final decision agent."

            return result
        except Exception as e:
            log.error(
                f"Final batch decision agent failed. Raw response: '{output_str}'. Error: {e}",
                exc_info=True,
            )
            return {}
