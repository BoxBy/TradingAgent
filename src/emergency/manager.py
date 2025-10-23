import json
import os
import time
from datetime import datetime, timedelta
from typing import Dict, List

from .. import config
from ..agents.market import (
    EmergencyNewsAgent,  # ✨ 수정된 클래스명 import
    MarketConditionAgent,
)
from ..data_providers import DataIngestion, TradingInterface
from ..utils import logger, notification
from ..utils.market_utils import is_kr_market_open, is_us_market_open
from .manager_llm import EmergencyManagerSystem2  # ✨ manager_llm import 추가

log = logger.get_logger(__name__)

PENDING_SELLS_FILE = os.path.join(config.LOG_DIR, "pending_emergency_sells.json")


class EmergencyManager:
    """
    주기적으로 시장 위험을 감시하고, 긴급 상황 발생 시 대응하는 클래스.
    """

    def __init__(
        self, kis_wrapper: TradingInterface, data_ingestion: DataIngestion, llm=None
    ):
        self.kis_wrapper = kis_wrapper
        self.data_ingestion = data_ingestion
        self.llm = llm

    def _load_pending_sells(self) -> List[Dict]:
        if not os.path.exists(PENDING_SELLS_FILE):
            return []
        try:
            with open(PENDING_SELLS_FILE, "r") as f:
                return json.load(f)
        except (json.JSONDecodeError, FileNotFoundError):
            return []

    def _save_pending_sells(self, pending_sells: List[Dict]):
        try:
            with open(PENDING_SELLS_FILE, "w") as f:
                json.dump(pending_sells, f, indent=4)
        except Exception as e:
            log.error(f"Failed to save pending sells queue: {e}")

    def check_for_emergency(self):
        """LLM이 결정한 동적 임계값과 개별 악재 뉴스를 사용하여 긴급 상황을 확인합니다."""
        log.info(
            "Checking for market-wide emergency situations using dynamic threshold..."
        )

        if not self.llm:
            log.error(
                "LLM is required for dynamic threshold assessment but not provided."
            )
            return

        # 1. 시장 전체 위기 확인 (VIX 지수 기반, 기존과 동일)
        # ... (이 부분은 이전과 동일하게 유지됩니다) ...
        current_vix = self.data_ingestion.get_vix_index()
        current_market_index = self.data_ingestion.get_market_index()
        market_news = self.data_ingestion.get_general_market_news("general")

        market_condition_agent = MarketConditionAgent(self.llm)
        assessment = market_condition_agent.analyze(
            current_vix, current_market_index, market_news
        )
        dynamic_threshold = assessment.get("dynamic_vix_threshold", 35.0)

        if current_vix and current_vix > dynamic_threshold:
            reason = (
                f"VIX Spike to {current_vix:.2f} (Threshold: {dynamic_threshold:.2f})"
            )
            log.warning(f"!!! EMERGENCY: {reason} !!!")

            if self.llm:
                log.info("Using LLM to selectively liquidate positions.")
                intelligent_manager = EmergencyManagerSystem2(
                    self.kis_wrapper, None, self.llm, None
                )
                intelligent_manager.handle_emergency_event(reason)
            else:
                self._liquidate_all_positions(reason)
            return

        # --- ✨ 2. 개별 종목 악재 확인 (LLM 기반으로 변경) ✨ ---
        log.info(
            "No market-wide emergency detected. Checking for individual stock news using LLM..."
        )
        portfolio = self.kis_wrapper.get_portfolio()
        if not portfolio:
            return

        # 방금 만든 EmergencyNewsAgent를 초기화합니다.
        emergency_news_agent = EmergencyNewsAgent(self.llm)
        end_date = datetime.now().strftime("%Y-%m-%d")
        start_date = (datetime.now() - timedelta(days=2)).strftime(
            "%Y-%m-%d"
        )  # 뉴스 조회 기간을 2일로 늘림

        for stock in portfolio:
            stock_code = stock["stock_code"]
            market_type = stock.get("market_type", "US")
            news = self.data_ingestion.get_company_news(
                stock_code, start_date, end_date
            )

            if news:
                # LLM 에이전트에게 뉴스 분석을 요청합니다.
                emergency_result = emergency_news_agent.analyze_news_for_emergency(
                    stock_code, news
                )

                # LLM이 긴급 상황이라고 판단하면, 해당 종목을 즉시 매도합니다.
                if emergency_result.get("is_emergency"):
                    reason = emergency_result.get(
                        "reason", "Critical negative news detected by LLM."
                    )
                    self._liquidate_specific_position(stock_code, reason, market_type)

            time.sleep(1)  # API 호출 간 짧은 딜레이

    def _liquidate_all_positions(self, reason: str):
        """
        포트폴리오의 모든 종목을 청산합니다.
        ✨ 각 종목의 시장 개장 여부를 확인하여 즉시 매도하거나 매도 대기열에 추가합니다. ✨
        """
        log.critical(f"--- LIQUIDATING ALL POSITIONS DUE TO: {reason} ---")
        portfolio = self.kis_wrapper.get_portfolio()
        if not portfolio:
            log.info("No positions in portfolio to liquidate.")
            return

        pending_sells = self._load_pending_sells()
        queued_count = 0
        sold_count = 0

        for stock in portfolio:
            stock_code = stock["stock_code"]
            market_type = stock.get(
                "market_type", "US"
            )  # 포트폴리오에서 시장 타입 확인

            is_market_open = (market_type == "US" and is_us_market_open()) or (
                market_type == "KR" and is_kr_market_open()
            )

            if is_market_open:
                # 장이 열려있으면 즉시 매도
                log.info(
                    f"Market for {stock_code} is open. Placing sell order immediately."
                )
                self.kis_wrapper.place_sell_order(
                    stock["stock_code"],
                    stock["quantity"],
                    price=stock["current_price"],
                    market=market_type,
                )
                sold_count += 1
            else:
                # 장이 닫혀있으면 대기열에 추가
                log.warning(
                    f"Market for {stock_code} is closed. Queuing emergency sell order."
                )
                if not any(p["stock_code"] == stock_code for p in pending_sells):
                    pending_sells.append(
                        {
                            "stock_code": stock_code,
                            "market_type": market_type,
                            "reason": f"Market-wide liquidation: {reason}",
                            "queued_at": datetime.now().isoformat(),
                        }
                    )
                    queued_count += 1

        # 변경된 대기열 저장
        if queued_count > 0:
            self._save_pending_sells(pending_sells)

        notification.send_notification(
            f"🚨 [긴급 전체 매도] 사유: {reason}. 즉시 매도: {sold_count}건, 매도 예약: {queued_count}건"
        )
        log.critical(
            f"--- All positions liquidation process finished. Sold: {sold_count}, Queued: {queued_count} ---"
        )

    def _liquidate_specific_position(
        self, stock_code: str, reason: str, market_type: str
    ):
        log.warning(f"--- LIQUIDATING {stock_code} DUE TO: {reason} ---")

        is_market_open = (market_type == "US" and is_us_market_open()) or (
            market_type == "KR" and is_kr_market_open()
        )

        portfolio = self.kis_wrapper.get_portfolio()
        stock_to_sell = next(
            (s for s in portfolio if s["stock_code"] == stock_code), None
        )

        if not stock_to_sell:
            log.warning(f"Could not find {stock_code} in portfolio to liquidate.")
            return

        if is_market_open:
            log.info(
                f"Market for {stock_code} is open. Placing sell order immediately."
            )
            self.kis_wrapper.place_sell_order(
                stock_to_sell["stock_code"],
                stock_to_sell["quantity"],
                price=stock_to_sell["current_price"],
                market=market_type,
            )
            notification.send_notification(
                f"🚨 [긴급 개별 매도] 종목: {stock_code}, 사유: {reason}"
            )
        else:
            log.warning(
                f"Market for {stock_code} is closed. Queuing emergency sell order."
            )
            pending_sells = self._load_pending_sells()
            if not any(p["stock_code"] == stock_code for p in pending_sells):
                pending_sells.append(
                    {
                        "stock_code": stock_code,
                        "market_type": market_type,
                        "reason": reason,
                        "queued_at": datetime.now().isoformat(),
                    }
                )
                self._save_pending_sells(pending_sells)
                notification.send_notification(
                    f"⏳ [긴급 매도 대기] {stock_code}을(를) 장 개시 후 매도하도록 예약했습니다. 사유: {reason}"
                )
