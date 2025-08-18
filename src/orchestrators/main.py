import json
from typing import Dict, List

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
from ..core import PortfolioOptimizer
from ..services import RAGManager
from ..utils import logger

log = logger.get_logger(__name__)


class TradingOrchestrator:
    """
    여러 분석 에이전트의 결과를 종합하고, 포트폴리오 최적화를 적용하여
    최종 매매 결정을 내리는 핵심 오케스트레이터.
    """

    def __init__(self, rag_manager: RAGManager, llm: BaseChatModel, tools: List):
        # 각 분석 단계를 수행할 에이전트들을 초기화합니다.
        self.rag_manager = rag_manager
        self.llm = llm
        self.tools = tools
        self.technical_agent = TechnicalAnalysisAgent()
        self.sentiment_agent = SentimentAnalysisAgent(llm)
        self.fundamental_agent = FundamentalAnalysisAgent(llm)
        self.qualitative_agent = QualitativeAnalysisAgent(llm)
        self.chart_pattern_agent = ChartPatternAgent(llm)

    def get_batch_trade_decisions(
        self, all_stocks_data: Dict[str, Dict], balance_info: Dict
    ) -> List[Dict]:
        """
        Watchlist의 모든 종목 데이터를 한번에 받아, 투자 결정 목록을 반환합니다.
        """
        log.info(f"--- Starting Batch Analysis for {list(all_stocks_data.keys())} ---")

        # 1. 각 종목에 대한 개별 분석을 먼저 수행합니다.
        comprehensive_analyses = {}
        for stock_code, data_bundle in all_stocks_data.items():
            comprehensive_analyses[stock_code] = {
                "technical_indicator_analysis": self.technical_agent.analyze(
                    data_bundle.get("ohlcv")
                ),
                "chart_pattern_analysis": self.chart_pattern_agent.analyze_chart_patterns(
                    data_bundle.get("ohlcv")
                ),
                "sentiment_analysis": self.sentiment_agent.analyze(
                    data_bundle.get("news")
                ),
                "fundamental_analysis": self.fundamental_agent.analyze(
                    data_bundle.get("fundamentals")
                ),
                "qualitative_analysis": self.qualitative_agent.analyze(
                    data_bundle.get("fundamentals"), data_bundle.get("news")
                ),
            }

        # 2. 모든 분석 결과를 종합하여 최종 결정을 내리는 LLM 에이전트를 호출합니다.
        final_decisions = self._make_final_batch_decision(
            comprehensive_analyses, balance_info
        )

        # 3. 매수 결정이 여러 개일 경우, 포트폴리오 최적화를 통해 투자 비중을 결정합니다.
        buy_decisions = [
            d
            for d in final_decisions
            if d.get("decision") == "BUY" and d.get("quantity", 0) > 0
        ]
        if len(buy_decisions) > 1:
            log.info("Optimizing portfolio weights for BUY decisions...")
            buy_stocks_data = {
                d["stock_code"]: all_stocks_data[d["stock_code"]]["ohlcv"]
                for d in buy_decisions
            }
            try:
                optimizer = PortfolioOptimizer(buy_stocks_data)
                optimal_weights = optimizer.find_optimal_weights_for_sharpe()

                # 계산된 최적 비중에 따라 수량을 재조정합니다.
                total_investment_amount = (
                    balance_info.get("cash_balance", 0) * 0.8
                )  # 현금의 80% 투자
                for decision in final_decisions:
                    stock_code = decision["stock_code"]
                    if stock_code in optimal_weights:
                        price_data = all_stocks_data[stock_code]["ohlcv"]
                        if price_data is not None and not price_data.empty:
                            stock_price = price_data.iloc[-1]["Close"]
                            investment_per_stock = (
                                total_investment_amount * optimal_weights[stock_code]
                            )
                            decision["quantity"] = (
                                int(investment_per_stock / stock_price)
                                if stock_price > 0
                                else 0
                            )
            except Exception as e:
                log.error(
                    f"Portfolio optimization failed: {e}. Proceeding without optimization."
                )

        # 4. 분석 결과와 최종 결정을 RAG DB에 저장합니다.
        for decision in final_decisions:
            stock_code = decision.get("stock_code")
            if stock_code in comprehensive_analyses:
                self.rag_manager.add_analysis_to_db(
                    stock_code,
                    json.dumps(comprehensive_analyses[stock_code], default=str),
                )
        return final_decisions

    def _make_final_batch_decision(self, analyses: Dict, balance: Dict) -> List[Dict]:
        """여러 종목의 분석 결과를 종합하여 각 종목별 '확신 점수'와 '투자 계획'을 반환하는 LLM 에이전트."""
        log.info("Running Final Batch Decision Agent...")

        relevant_insights = []
        for stock_code in analyses.keys():
            query = json.dumps(analyses[stock_code].get("sentiment_analysis", {}))
            insights = self.rag_manager.retrieve_relevant_insights(stock_code, query)
            if insights:
                relevant_insights.extend(insights)

        prompt = ChatPromptTemplate.from_messages(
            [
                (
                    "system",
                    "You are a self-improving, highly aggressive, short-term momentum trader AI. "
                    "Your goal is to maximize profits by acting decisively on market catalysts. "
                    "Your primary focus is on short-term (days to weeks) gains. "
                    "You are willing to take on calculated risks for higher returns. "
                    "Analyze the batch of stocks and decide which ones to BUY or SELL based on the provided analyses and user rules. "
                    "Prioritize stocks with strong positive news sentiment and bullish technical indicators. "
                    "If a stock has negative news or bearish signals, decide to SELL if it's in the portfolio. "
                    "If information is insufficient or neutral, your decision should be 'HOLD'.\n\n"
                    "**CRITICAL INSTRUCTIONS:**\n"
                    "1.  For **every** stock, you **MUST** provide a `conviction_score` from -10 (Strong Sell) to +10 (Strong Buy).\n"
                    "2.  Review 'Past Trading Insights'. Use these successes and failures to inform your scores.\n"
                    "3.  For each stock, provide a 'decision', 'reasoning', 'target_gain_percentage' (if BUY), "
                    "'stop_loss_percentage' (if BUY), and 'sell_deadline_date' (if BUY).\n\n"
                    "Your final output **MUST** be a single, valid JSON object with a 'decisions' key holding a list of plans.\n\n"
                    "JSON Output Format:\n"
                    '{{ "decisions": [ \n'
                    '  {{ "stock_code": "<TICKER1>", "decision": "BUY"|"SELL"|"HOLD", "conviction_score": <float>, "quantity": <int>, '
                    '"target_gain_percentage": <float, if BUY>, '
                    '"stop_loss_percentage": <float, if BUY>, '
                    '"sell_deadline_date": "<YYYY-MM-DD, if BUY>", "reasoning": "..." }},\n'
                    '  {{ "stock_code": "<TICKER2>", ... }}\n'
                    "]}}",
                ),
                (
                    "human",
                    "Analyze the following stocks and provide your trading decisions.\n"
                    "Account Status: {account_status}\n"
                    "User Investment Rules (currently set to '{trading_style}' style): {user_rules}\n"
                    "**Past Trading Insights (Learn from these!):**\n{past_insights}\n"
                    "Comprehensive Analyses for all stocks:\n{comprehensive_analyses}",
                ),
                MessagesPlaceholder(variable_name="agent_scratchpad"),
            ]
        )

        agent = create_tool_calling_agent(self.llm.provider, self.tools, prompt)
        agent_executor = AgentExecutor(
            agent=agent, tools=self.tools, verbose=True, handle_parsing_errors=True
        )

        output_str = ""
        try:
            response = agent_executor.invoke(
                {
                    "account_status": json.dumps(balance),
                    "trading_style": config.TRADING_STYLE,
                    "user_rules": json.dumps(config.USER_RULES),
                    "past_insights": (
                        "\n".join(relevant_insights)
                        if relevant_insights
                        else "No past insights available."
                    ),
                    "comprehensive_analyses": json.dumps(analyses, default=str),
                }
            )
            output_str = response.get("output", "{}")

            if not output_str or not output_str.strip():
                log.warning(
                    "Final batch decision agent returned an empty response. Returning empty list."
                )
                return []  # 비어있는 리스트를 반환하여 오류를 방지

            if "```json" in output_str:
                output_str = output_str.split("```json")[1].split("```")[0].strip()

            result = json.loads(output_str)

            return result.get("decisions", [])
        except Exception as e:
            log.error(
                f"Final batch decision agent failed. Raw response: '{output_str}'. Error: {e}",
                exc_info=True,
            )
            raise e
