import time
from datetime import datetime, timedelta

from ..data_providers import TradingInterface, DataIngestion
from ..utils import logger, notification
from ..agents.market import MarketConditionAgent, EmergencyNewsAgent # ✨ 수정된 클래스명 import
from .manager_llm import EmergencyManagerSystem2 # ✨ manager_llm import 추가

log = logger.get_logger(__name__)

class EmergencyManager:
    """
    주기적으로 시장 위험을 감시하고, 긴급 상황 발생 시 대응하는 클래스.
    """
    def __init__(self, kis_wrapper: TradingInterface, data_ingestion: DataIngestion, llm=None):
        self.kis_wrapper = kis_wrapper
        self.data_ingestion = data_ingestion
        self.llm = llm

    def check_for_emergency(self):
        """LLM이 결정한 동적 임계값과 개별 악재 뉴스를 사용하여 긴급 상황을 확인합니다."""
        log.info("Checking for market-wide emergency situations using dynamic threshold...")
        
        if not self.llm:
            log.error("LLM is required for dynamic threshold assessment but not provided.")
            return

        # 1. 시장 전체 위기 확인 (VIX 지수 기반, 기존과 동일)
        # ... (이 부분은 이전과 동일하게 유지됩니다) ...
        current_vix = self.data_ingestion.get_vix_index()
        current_market_index = self.data_ingestion.get_market_index()
        market_news = self.data_ingestion.get_general_market_news('general')

        market_condition_agent = MarketConditionAgent(self.llm)
        assessment = market_condition_agent.analyze(current_vix, current_market_index, market_news)
        dynamic_threshold = assessment.get("dynamic_vix_threshold", 35.0)
        
        if current_vix and current_vix > dynamic_threshold:
            reason = f"VIX Spike to {current_vix:.2f} (Threshold: {dynamic_threshold:.2f})"
            log.warning(f"!!! EMERGENCY: {reason} !!!")
            
            if self.llm:
                log.info("Using LLM to selectively liquidate positions.")
                intelligent_manager = EmergencyManagerSystem2(self.kis_wrapper, None, self.llm, None) 
                intelligent_manager.handle_emergency_event(reason)
            else:
                self._liquidate_all_positions(reason)
            return

        # --- ✨ 2. 개별 종목 악재 확인 (LLM 기반으로 변경) ✨ ---
        log.info("No market-wide emergency detected. Checking for individual stock news using LLM...")
        portfolio = self.kis_wrapper.get_portfolio()
        if not portfolio:
            return

        # 방금 만든 EmergencyNewsAgent를 초기화합니다.
        emergency_news_agent = EmergencyNewsAgent(self.llm)
        end_date = datetime.now().strftime('%Y-%m-%d')
        start_date = (datetime.now() - timedelta(days=2)).strftime('%Y-%m-%d') # 뉴스 조회 기간을 2일로 늘림

        for stock in portfolio:
            stock_code = stock['stock_code']
            news = self.data_ingestion.get_company_news(stock_code, start_date, end_date)
            
            if news:
                # LLM 에이전트에게 뉴스 분석을 요청합니다.
                emergency_result = emergency_news_agent.analyze_news_for_emergency(stock_code, news)
                
                # LLM이 긴급 상황이라고 판단하면, 해당 종목을 즉시 매도합니다.
                if emergency_result.get("is_emergency"):
                    reason = emergency_result.get("reason", "Critical negative news detected by LLM.")
                    self._liquidate_specific_position(stock_code, reason)
            
            time.sleep(1) # API 호출 간 짧은 딜레이

    def _liquidate_all_positions(self, reason: str):
        # 포트폴리오의 모든 종목을 청산합니다.
        log.critical(f"--- LIQUIDATING ALL POSITIONS DUE TO: {reason} ---")
        portfolio = self.kis_wrapper.get_portfolio()
        for stock in portfolio:
            self.kis_wrapper.place_sell_order(stock['stock_code'], stock['quantity'], price=stock['current_price'], market='US')
        notification.send_notification(f"🚨 [긴급 전체 매도] 사유: {reason}")
        log.critical("--- All positions liquidated. ---")

    def _liquidate_specific_position(self, stock_code: str, reason: str):
        # 특정 종목을 청산합니다.
        log.warning(f"--- LIQUIDATING {stock_code} DUE TO: {reason} ---")
        portfolio = self.kis_wrapper.get_portfolio()
        for stock in portfolio:
            if stock['stock_code'] == stock_code:
                self.kis_wrapper.place_sell_order(stock['stock_code'], stock['quantity'], price=stock['current_price'], market='US')
                log.warning(f"--- {stock_code} liquidated. ---")
                break
        notification.send_notification(f"🚨 [긴급 개별 매도] 종목: {stock_code}, 사유: {reason}")