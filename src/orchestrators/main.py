import json
from typing import Dict, List
from datetime import datetime
import math

from ..agents.market import PortfolioReviewAgent
from .. import prompts

from langchain.agents import AgentExecutor, create_tool_calling_agent
from langchain.prompts import ChatPromptTemplate, MessagesPlaceholder
from langchain_core.language_models.chat_models import BaseChatModel

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


class TradingOrchestrator:
    """
    여러 분석 에이전트의 결과를 종합하고, 포트폴리오 최적화를 적용하여
    최종 매매 결정을 내리는 핵심 오케스트레이터.
    """

    def __init__(self, rag_manager: RAGManager, llm: BaseChatModel, tools: List):
        self.rag_manager = rag_manager
        self.llm = llm
        self.tools = tools
        self.technical_agent = TechnicalAnalysisAgent()
        self.sentiment_agent = SentimentAnalysisAgent(llm)
        self.fundamental_agent = FundamentalAnalysisAgent(llm)
        self.qualitative_agent = QualitativeAnalysisAgent(llm)
        self.chart_pattern_agent = ChartPatternAgent(llm)
        self.review_agent = PortfolioReviewAgent(llm)

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
                
                FINAL_BUY_THRESHOLD = 6.0 
                if final_score < FINAL_BUY_THRESHOLD:
                    decision["decision"] = "HOLD"
                    decision["reasoning"] = f"[SCORE TOO LOW] Final score {final_score:.2f} is below threshold {FINAL_BUY_THRESHOLD}. Original Reason: {decision.get('reasoning')}"
                    log.warning(f"BUY decision for {stock_code} converted to HOLD due to low final score.")
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

                # 확신 점수를 가중치로 사용하여 총 투자금을 종목별로 분배합니다.
                total_score = sum(d.get("conviction_score", 5) for d in buy_decisions)
                if total_score == 0:
                    total_score = 1 # 0으로 나누는 오류 방지

                for decision in buy_decisions:
                    stock_code = decision["stock_code"]
                    price_data = all_stocks_data[stock_code]["ohlcv"]
                    if price_data is not None and not price_data.empty:
                        stock_price = price_data.iloc[-1]["Close"]
                        conviction_score = decision.get("conviction_score", 5)

                        # 확신 점수 비율에 따라 종목별 투자금 할당
                        investment_per_stock = total_investment_amount * (conviction_score / total_score)
                        
                        # config의 종목당 최대 투자 한도를 여전히 존중
                        investment_per_stock = min(investment_per_stock, config.USER_RULES.get("max_investment_per_stock", float('inf')))

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

        prompt = ChatPromptTemplate.from_messages([
            ("system", prompts.FINAL_BATCH_DECISION_PROMPT),
            ("human", 
             "Today's Date: {current_date} (Current Time: {current_time} KST)\n\n"
             "Analyze the following stocks and provide your trading decisions.\n"
             "Maximum Investable Cash for this Batch: {max_investable_cash:,.0f} {currency}\n"
             "Account Status: {account_status}\n"
             "User Investment Rules (currently set to '{trading_style}' style): {user_rules}\n"
             "**Past Trading Insights (Learn from these!):**\n{past_insights}\n"
             "Comprehensive Analyses for all stocks (including general review history):\n{comprehensive_analyses}"),
            MessagesPlaceholder(variable_name="agent_scratchpad"),
        ])

        agent = create_tool_calling_agent(self.llm, self.tools, prompt)
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
                "past_insights": "\n".join(relevant_insights) if relevant_insights else "No past insights available.",
                "critical_events": critical_events,
                "comprehensive_analyses": json.dumps(analyses, default=str)
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

            return result
        except Exception as e:
            log.error(
                f"Final batch decision agent failed. Raw response: '{output_str}'. Error: {e}",
                exc_info=True,
            )
            return {}
