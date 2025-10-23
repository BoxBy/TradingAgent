import json

from langchain.prompts import ChatPromptTemplate
from langchain.schema.output_parser import StrOutputParser
from langchain_core.language_models.chat_models import BaseChatModel

from ..core import TradeMonitor
from ..data_providers import TradingInterface
from ..utils import logger, notification
from .. import prompts

log = logger.get_logger(__name__)


class EmergencyManagerSystem2:
    def __init__(
        self,
        kis_wrapper: TradingInterface,
        trade_monitor: TradeMonitor,
        llm: BaseChatModel,
        trade_logger: logger.TradeLogger,
    ):
        self.kis_wrapper = kis_wrapper
        self.trade_monitor = trade_monitor
        self.logger = trade_logger
        self.llm = llm

    def handle_emergency_event(self, event_description: str):
        log.info(f"--- Handling Emergency Event with LLM: {event_description} ---")

        portfolio = self.kis_wrapper.get_portfolio()
        if not portfolio:
            log.info("No positions in portfolio to analyze for emergency event.")
            return

        portfolio_codes = [stock["stock_code"] for stock in portfolio]

        affected_stocks = self._identify_affected_stocks(
            event_description, portfolio_codes
        )
        if not affected_stocks:
            log.info("LLM determined no stocks are affected by this event.")
            return

        log.info(f"Affected stocks identified by LLM: {affected_stocks}")

        for stock_code in affected_stocks:
            if stock_code not in portfolio_codes:
                continue

            analysis_result = self._analyze_impact(event_description, stock_code)
            impact = analysis_result.get("impact")
            reasoning = analysis_result.get("reasoning", "LLM analysis")

            stock_info = next(
                (s for s in portfolio if s["stock_code"] == stock_code), None
            )
            if not stock_info:
                continue

            if impact == "negative":
                log.warning(
                    f"Impact on {stock_code} is NEGATIVE. Liquidating position based on LLM analysis."
                )
                quantity = stock_info["quantity"]
                current_price = stock_info["current_price"]

                # ✨ 긴급 선별 매도 알림 추가 ✨
                msg = (
                    f"🚨 [긴급 선별 매도] 종목: {stock_code}\n"
                    f"사유: {event_description}\n"
                    f"LLM 분석: {reasoning}"
                )
                notification.send_notification(msg)

                self.kis_wrapper.place_sell_order(
                    stock_code, quantity, price=current_price, market="US"
                )
                self.logger.log_trade(
                    stock_code,
                    "SELL",
                    quantity,
                    current_price,
                    f"Emergency LLM Sell: {event_description}",
                )
                if stock_code in self.trade_monitor.active_trades:
                    del self.trade_monitor.active_trades[stock_code]
                    self.trade_monitor._save_state()

            elif impact == "positive":
                log.info(f"Impact on {stock_code} is POSITIVE. Readjusting targets.")
                new_gain = analysis_result.get("new_target_gain_percentage")
                new_deadline = analysis_result.get("new_sell_deadline_date")
                if (
                    new_gain
                    and new_deadline
                    and stock_code in self.trade_monitor.active_trades
                ):
                    self.trade_monitor.update_trade_targets(
                        stock_code, new_gain, new_deadline
                    )
                else:
                    log.warning(
                        f"Could not readjust targets for {stock_code} due to invalid LLM response or not in active trades."
                    )

    def _identify_affected_stocks(self, event: str, portfolio: list) -> list:
        prompt = ChatPromptTemplate.from_template(prompts.IDENTIFY_AFFECTED_STOCKS_PROMPT)
        chain = prompt | self.llm | StrOutputParser()
        response_str = chain.invoke({"event": event, "portfolio": ", ".join(portfolio)})
        try:
            return json.loads(response_str)
        except:
            return []

    def _analyze_impact(self, event: str, stock_code: str) -> dict:
        prompt = ChatPromptTemplate.from_template(prompts.ANALYZE_IMPACT_PROMPT)
        chain = prompt | self.llm | StrOutputParser()
        response_str = chain.invoke({"event": event, "stock_code": stock_code})
        try:
            return json.loads(response_str)
        except:
            return {"impact": "unknown"}