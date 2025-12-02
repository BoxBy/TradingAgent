import json
import os
import time
from datetime import datetime, timedelta
from functools import partial

import pandas as pd
import schedule
import random

from src import config
from src.agents.market import (
    MarketConditionAgent,
    NewsScreenerAgent,
    PortfolioReviewAgent,
)
from src.agents.tools import StockAnalysisTools
from src.core import LLMStrategyBacktester, TradeMonitor
from src.data_providers import DataIngestion, TradingInterface
from src.emergency import PENDING_SELLS_FILE, EmergencyManager
from src.orchestrators import TradingOrchestrator
from src.services import RAGManager
from src.services.threshold_store import ThresholdStore
from src.services.database_manager import NewsDatabase
from src.utils import logger, notification, reporting, ticker_utils
from src.utils.api_key_manager import LLMProvider, runnable_llm_provider
from src.utils.market_utils import is_kr_market_open, is_us_market_open

# 봇의 모든 로깅을 관리하는 로거 인스턴스를 생성합니다.
log = logger.get_logger(__name__)

BASELINE_FILE = "asset_baseline.json"
ASSET_HISTORY_FILE = os.path.join(config.LOG_DIR, "asset_history.json")
ASSET_HISTORY_FILE_KR = os.path.join(config.LOG_DIR, "asset_history_kr.json")
ASSET_HISTORY_FILE_US = os.path.join(config.LOG_DIR, "asset_history_us.json")

def news_collection_job(kis_api: TradingInterface, db: NewsDatabase):
    """
    24시간 내내 주기적으로 시장 뉴스를 수집하여 데이터베이스에 저장합니다.
    (Finnhub 일반 뉴스 + Naver 종목 뉴스)
    """
    log.info("--- Running News Collection Job ---")
    data_ingestor = DataIngestion(kis_api)
    try:
        # Finnhub에서 일반 시장 뉴스 수집
        market_news = data_ingestor.get_general_market_news("general")
        if market_news:
            db.save_news(market_news)
    except Exception as e:
        # Finnhub API 오류가 발생하더라도, 프로그램을 중단하지 않고 경고만 기록합니다.
        log.warning(
            f"Could not fetch general market news from Finnhub due to an API error: {e}"
        )

    # 2. Naver에서 KOSPI 종목 뉴스 샘플 수집
    try:
        kospi_tickers_path = os.path.join(config.DATA_DIR, "kospi_tickers.csv")
        if os.path.exists(kospi_tickers_path):
            kospi_df = pd.read_csv(kospi_tickers_path)
            sample_size = min(10, len(kospi_df))
            sampled_stocks = kospi_df.sample(n=sample_size)

            for _, row in sampled_stocks.iterrows():
                stock_name = row["종목명"]
                naver_news = data_ingestor.get_news_from_naver(stock_name)
                if naver_news:
                    db.save_news(naver_news)
                time.sleep(0.5)
        else:
            log.warning("KOSPI 종목 리스트 파일이 없어 Naver 뉴스 수집을 건너뜁니다.")
    except Exception as e:
        log.error(f"Error in Naver news collection part of the job: {e}", exc_info=True)


# --- 각 시장별 job 함수 정의 ---
def kr_decision_making_job(
    kis_api, llm_provider: LLMProvider, rag_manager, trade_monitor, db
):
    """한국 주식 시장에 대한 의사결정 프로세스를 실행합니다."""
    run_decision_process("KR", kis_api, llm_provider, rag_manager, trade_monitor, db)


def us_decision_making_job(
    kis_api, llm_provider: LLMProvider, rag_manager, trade_monitor, db
):
    """미국 주식 시장에 대한 의사결정 프로세스를 실행합니다."""
    run_decision_process("US", kis_api, llm_provider, rag_manager, trade_monitor, db)

def run_decision_process(
    market_type: str,
    kis_api: TradingInterface,
    llm_provider: LLMProvider,
    rag_manager: RAGManager,
    trade_monitor: TradeMonitor,
    db: NewsDatabase,
):
    """
    지정된 시장(US 또는 KR)에 대한 전체 의사결정 프로세스를 실행하고, API 오류 발생 시 키 전환을 시도합니다.
    """
    if (market_type == "US" and not is_us_market_open()) or (
        market_type == "KR" and not is_kr_market_open()
    ):
        log.info(f"Decision job for {market_type} market skipped: Market closed.")
        return

    job_start_time = datetime.now()
    log.info(
        f"\n--- Running Decision Job for {market_type} Market at {job_start_time.strftime('%Y-%m-%d %H:%M:%S')} ---"
    )

    try:
        unprocessed_news = db.get_unprocessed_news()
        if not unprocessed_news:
            log.info("No new unprocessed news to analyze. Skipping decision job.")
            return

        notification.send_notification(
            f"🚀 [System 3] {market_type} 시장 분석 시작 ({len(unprocessed_news)}개 신규 뉴스)"
        )

        data_ingestor = DataIngestion(kis_api)

        news_screener = NewsScreenerAgent(llm_provider)
        watchlist_dict = news_screener.generate_watchlist_from_news(unprocessed_news, market_type=market_type)

        watchlist = watchlist_dict.get(market_type.lower(), [])

        if not watchlist:
            log.info(
                f"No relevant {market_type} stocks found from news. Skipping analysis."
            )
            news_ids_to_mark = [news["id"] for news in unprocessed_news]
            db.mark_news_as_processed(news_ids_to_mark)
            return
        else:
            try:
                # 1. ticker_utils에서 해당 시장의 전체 종목 리스트를 가져옴 (파일 읽기 X)
                all_tickers_list = ticker_utils.get_tickers_by_market(market_type)

                if all_tickers_list:
                    # 2. 추가할 랜덤 종목 수 계산
                    num_random_to_add = max(1, int(len(watchlist) * 0.5))
                    if num_random_to_add > 0:
                        # 3. 후보군 생성
                        candidate_pool = [
                            ticker for ticker in all_tickers_list
                            if ticker not in watchlist and ticker not in trade_monitor.active_trades
                        ]
                        # 4. 랜덤 종목 선정
                        if len(candidate_pool) >= num_random_to_add:
                            random_stocks = random.sample(candidate_pool, num_random_to_add)
                            watchlist.extend(random_stocks)
                            log.info(f"Added {num_random_to_add} random 'exploration' stocks to watchlist: {random_stocks}")
                        else:
                            log.warning(f"Could not add random stocks. Not enough candidates in the pool.")
            except Exception as e:
                log.error(f"Error adding random stocks to watchlist: {e}", exc_info=True)


        tools = StockAnalysisTools(data_ingestor, llm=llm_provider).get_tools()
        orchestrator = TradingOrchestrator(rag_manager, llm_provider, tools)

        recent_start_date = (datetime.now() - timedelta(days=90)).strftime("%Y-%m-%d")
        recent_end_date = datetime.now().strftime("%Y-%m-%d")
        all_stocks_data = {}
        for stock in watchlist:
            if stock in trade_monitor.active_trades:
                continue
            ohlcv = data_ingestor.get_technical_data(stock, market=market_type)
            company_news = data_ingestor.get_company_news(
                stock,
                recent_start_date,
                recent_end_date,
            )
            stock_name = ticker_utils.get_stock_name(stock)
            related_news = []
            for news in unprocessed_news:
                headline = news.get("headline") or ""
                summary = news.get("summary") or ""
                if stock_name and stock_name in headline:
                    related_news.append(news)
                elif stock in headline:
                    related_news.append(news)
                elif stock_name and stock_name in summary:
                    related_news.append(news)
            combined_news = []
            if company_news:
                combined_news.extend(company_news)
            if related_news:
                combined_news.extend(related_news)

            fundamental_symbol = stock
            if market_type == "KR":
                fundamental_symbol = f"{stock}.KS"
            fundamentals = data_ingestor.get_fundamental_data(fundamental_symbol)

            all_stocks_data[stock] = {
                "ohlcv": ohlcv,
                "news": combined_news,
                "fundamentals": fundamentals,
                "current_price": kis_api.fetch_price(stock, market=market_type),
            }

        if not all_stocks_data:
            log.info("No new stocks to analyze.")
            news_ids_to_mark = [news["id"] for news in unprocessed_news]
            db.mark_news_as_processed(news_ids_to_mark)
            return

        balance_info = kis_api.get_balance(market_type="ALL")

        if not balance_info or "total_assets" not in balance_info:
            log.error("계좌 잔고 조회에 최종 실패하여 이번 작업을 중단합니다.")
            return
    
        log.info(f'balance_info : {balance_info}')

        daily_asset_baseline = load_previous_day_closing_assets()
        report_message = reporting.format_balance_for_slack(
            balance_info, daily_asset_baseline
        )
        notification.send_notification(report_message)

        kr_assets = balance_info.get("kr_total_krw", 0) or 0
        us_assets = balance_info.get("us_total_krw", 0) or 0
        total_assets = balance_info.get("total_assets", kr_assets + us_assets)

        raw_cash_balance = balance_info.get("cash_balance", 0)
        cash_kr = balance_info.get("cash_kr_krw", 0)
        cash_us = balance_info.get("cash_us_krw", 0)

        # 시장별 의사결정 시, 해당 시장의 현금만 사용합니다.
        if market_type == "KR":
            cash_balance = cash_kr if cash_kr else raw_cash_balance
        elif market_type == "US":
            cash_balance = cash_us if cash_us else raw_cash_balance
        else:
            cash_balance = raw_cash_balance

        # config.py에서 설정한 최소 보유 현금 비율을 가져옵니다.
        # 각 시장 의사결정 시, 해당 시장 "보유 종목 자산"을 기준으로 예비금을 계산합니다.
        # 보유 종목이 전혀 없다면, 예비금을 0으로 두고 현금 전액을 투자 가능 금액으로 간주합니다.
        min_reserve_ratio = config.USER_RULES.get("min_cash_reserve_ratio", 0.2)
        portfolio_kr = balance_info.get("portfolio_kr") or []
        portfolio_us = balance_info.get("portfolio_us") or []

        if market_type == "KR":
            base_assets_for_reserve = kr_assets if portfolio_kr else 0
        elif market_type == "US":
            base_assets_for_reserve = us_assets if portfolio_us else 0
        else:
            base_assets_for_reserve = total_assets

        reserve_cash_krw = base_assets_for_reserve * min_reserve_ratio  # 예비금은 KRW 기준으로 계산

        # 예비금 차감 후 음수 방지 클램프
        investable_krw = max(0, cash_balance - reserve_cash_krw)

        if market_type == "US":
            fx = kis_api.get_exchange_rate() or 1300.0
            max_investable_cash = investable_krw / fx
        else:
            max_investable_cash = investable_krw

        if market_type == "KR":
            current_assets = balance_info.get("kr_total_krw", total_assets)
            history_file = ASSET_HISTORY_FILE_KR
        else:
            current_assets = balance_info.get("us_total_krw", total_assets)
            history_file = ASSET_HISTORY_FILE_US

        rolling_metrics = compute_rolling_30d_metrics(current_assets, history_file)
        base_equity_30d = rolling_metrics.get("base_equity_30d", total_assets)
        pnl_30d_pct = rolling_metrics.get("pnl_30d_pct", 0.0)
        max_dd_30d_pct = rolling_metrics.get("max_drawdown_30d_pct", 0.0)
        base_dd_limit_pct = 10.0
        effective_dd_limit_pct = base_dd_limit_pct
        if pnl_30d_pct <= -base_dd_limit_pct:
            effective_dd_limit_pct = 8.0
        elif pnl_30d_pct >= 15.0:
            effective_dd_limit_pct = 12.0
        risk_scaler = 1.0
        
        # 해외 자산 조회 실패(또는 미실행)로 인해 전체 자산 규모가 왜곡될 수 있으므로,
        # 이 경우 리스크 스케일러를 강제로 1.0으로 설정하여 투자를 지속합니다.
        if balance_info.get("us_total_krw") is False:
            log.warning("해외 자산 조회 실패로 인해 자산 변동폭 계산이 부정확할 수 있습니다. Risk Scaler를 1.0으로 고정합니다.")
            risk_scaler = 1.0
        else:
            if pnl_30d_pct <= -effective_dd_limit_pct:
                risk_scaler = 0.0
            elif pnl_30d_pct <= -(effective_dd_limit_pct * 0.5):
                risk_scaler = 0.5

        max_investable_cash = max_investable_cash * risk_scaler

        balance_info["rolling_30d_base_equity"] = base_equity_30d
        balance_info["rolling_30d_pnl_percent"] = pnl_30d_pct
        balance_info["rolling_30d_max_drawdown_percent"] = max_dd_30d_pct
        balance_info["monthly_return_target_percent"] = 20.0
        balance_info["monthly_drawdown_limit_percent"] = effective_dd_limit_pct

        if max_investable_cash <= 10000: # 최소 투자금액보다 적으면 중단
            log.info(f"Not enough investable cash ({max_investable_cash:,.0f} KRW or USD) after reserving cash. Skipping new buy decisions.")
            news_ids_to_mark = [news['id'] for news in unprocessed_news]
            db.mark_news_as_processed(news_ids_to_mark)
            return
        # ✨ --- 로직 종료 ---

        # --- 통합형 ThresholdStore + MarketConditionAgent 기반 BUY/SELL 게이트 계산 ---
        market_condition_agent = MarketConditionAgent(llm_provider)
        current_vix = data_ingestor.get_vix_index()
        market_index_val = data_ingestor.get_market_index()

        buy_threshold = None
        sell_threshold = None
        market_conditions = {}
        suppress_reason_in_market_msg = False

        try:
            if market_type == "KR":
                equity_for_ratio = kr_assets or total_assets
            elif market_type == "US":
                equity_for_ratio = us_assets or total_assets
            else:
                equity_for_ratio = total_assets

            cash_ratio = (cash_balance / equity_for_ratio) if equity_for_ratio else 0.0
            exposure_level = float(len(trade_monitor.active_trades))
            recent_fill_stats = rag_manager.get_recent_fill_stats(days=3)
            ce_list = rag_manager.retrieve_recent_critical_events(watchlist, days=1)
            critical_events_text = "\n".join(ce_list) if ce_list else ""

            assessment = market_condition_agent.analyze(
                current_vix,
                market_index_val,
                unprocessed_news,
                cash_ratio=cash_ratio,
                exposure_level=exposure_level,
                recent_fill_stats=recent_fill_stats,
                critical_events=critical_events_text,
            )

            buy_threshold = float(assessment.get("buy_conviction_threshold", 6.0))
            sell_threshold = float(assessment.get("sell_conviction_threshold", -6.0))
            confidence = float(assessment.get("confidence", 0.7))
            ttl_minutes = int(assessment.get("ttl_minutes", 90))
            reasoning = assessment.get("reasoning", "")

            market_conditions = assessment
            # 오케스트레이터에서 VIX 기반 가중치를 제대로 사용하기 위해 현재 VIX 값을 함께 전달합니다.
            try:
                market_conditions["vix_index"] = float(current_vix) if current_vix is not None else 15.0
            except Exception:
                market_conditions["vix_index"] = 15.0

            # TTL 만료 후 새로 LLM을 호출한 경우 슬랙 알림 전송
            try:
                msg = (
                    "📊 *정기 시장 위험 보고*\n"
                    f"- 현재 VIX: *{(current_vix or 0.0):.2f}*\n"
                    f"- 동적 VIX 임계값: *{assessment.get('dynamic_vix_threshold', 35.0):.2f}*\n"
                    f"- BUY 게이트: *{buy_threshold:.2f}*\n"
                    f"- SELL 게이트: *{sell_threshold:.2f}*\n"
                    f"- Reason: {reasoning or 'N/A'}"
                )
                notification.send_notification(msg)
                # 방금 위에서 임계값과 이유를 슬랙으로 보냈으므로,
                # 아래의 [Market Condition] 요약에서는 같은 이유를 반복하지 않도록 플래그 설정
                suppress_reason_in_market_msg = True
            except Exception as e:
                log.warning(f"Failed to send buy threshold update to Slack: {e}")
        except Exception as e:
            log.error(f"Failed to compute integrated thresholds with LLM: {e}", exc_info=True)
            buy_threshold = 6.0
            sell_threshold = -6.0
            market_conditions = {
                "dynamic_vix_threshold": 35.0,
                "buy_conviction_threshold": buy_threshold,
                "sell_conviction_threshold": sell_threshold,
                "reasoning": "fallback_default",
            }

        dyn_vix_th = market_conditions.get("dynamic_vix_threshold")

        # ✨ Orchestrator에 '투자 가능 최대 금액'을 전달합니다.
        final_decisions_payload = orchestrator.get_batch_trade_decisions(
            all_stocks_data,
            balance_info,
            market_conditions,
            max_investable_cash * 0.9,
            market_type,
        )
        log.info(f"final_decisions_payload : {final_decisions_payload}")
        final_decisions = final_decisions_payload.get("decisions", [])
        # Always notify Slack with the final decisions summary
        try:
            decisions_msg = reporting.format_decisions_for_slack(
                final_decisions,
                market_type,
                buy_threshold=buy_threshold,
                sell_threshold=sell_threshold,
            )
            notification.send_notification(decisions_msg)
        except Exception as e:
            log.warning(f"Failed to format/send final decisions to Slack: {e}")
        
        # 실시간으로 차감할 가용 현금 변수 생성
        live_investable_cash = max_investable_cash 

        for decision in final_decisions:
            score = decision.get("conviction_score", 0)
            stock_code = decision.get("stock_code")
            reasoning = decision.get("reasoning", "No reasoning provided.")
            quantity = decision.get("quantity", 0)
            decision_type = decision.get("decision", "HOLD")

            # --- 매수 결정 로직 ---
            if decision_type == "BUY" and score >= buy_threshold and quantity > 0:
                current_price = all_stocks_data.get(stock_code, {}).get("current_price")

                # 1. 현재 가격이 유효한 숫자인지 확인
                if current_price and current_price > 0:
                    order_cost = quantity * current_price

                    # 2. 주문 비용이 현재 남은 가용 현금보다 적은지 확인
                    if order_cost <= live_investable_cash:
                        log.info(
                            f"Executing BUY for {quantity} shares of {stock_code} based on conviction score ({score} >= {buy_threshold})."
                        )
                        order_result = kis_api.place_buy_order(
                            stock_code,
                            quantity,
                            price=current_price,
                            market=market_type,
                        )
                        if order_result:
                            # 3. 주문 성공 시 가용 현금에서 비용 차감
                            live_investable_cash -= order_cost
                            log.info(f"Order successful. Remaining cash: {live_investable_cash:,.0f}")
                            
                            msg = f"✅ [System 3 매수] {ticker_utils.format_for_slack(stock_code)}: {quantity}주 @ {'$' if market_type == 'US' else '₩'}{current_price:,.2f}. 이유: {reasoning}"
                            trade_monitor.logger.log_trade(
                                stock_code, "BUY", quantity, current_price, reasoning
                            )
                            trade_monitor.register_trade(
                                stock_code=stock_code,
                                purchase_price=current_price,
                                quantity=quantity,
                                target_gain_percentage=decision.get(
                                    "target_gain_percentage",
                                    config.USER_RULES[
                                        "target_profit_percent_per_trade"
                                    ],
                                ),
                                stop_loss_value=decision.get(
                                    "stop_loss_percentage",
                                    config.USER_RULES["max_loss_percent_per_trade"],
                                ),
                                sell_deadline_date=decision.get(
                                    "sell_deadline_date",
                                    (datetime.now() + timedelta(days=7)).strftime(
                                        "%Y-%m-%d"
                                    ),
                                ),
                                reasoning=reasoning,
                                market_type=market_type,
                            )
                            notification.send_notification(msg)
                    else:
                        log.warning(
                            f"Skipping BUY for {stock_code}. "
                            f"Order cost ({order_cost:,.0f}) exceeds remaining investable cash ({live_investable_cash:,.0f})."
                        )
                else:
                    log.error(f"Cannot place buy order for {stock_code} due to invalid current_price: {current_price}")
            
            # --- 매도 결정 로직 ---
            elif score <= sell_threshold:
                stock_to_sell = next(
                    (
                        s
                        for s in balance_info.get("portfolio", [])
                        if s["stock_code"] == stock_code
                    ),
                    None,
                )
                if stock_to_sell:
                    log.info(
                        f"Executing SELL for {stock_code} based on conviction score ({score} <= {sell_threshold})."
                    )
                    current_price = stock_to_sell["current_price"]
                    quantity_to_sell = stock_to_sell["quantity"]
                    kis_api.place_sell_order(
                        stock_code,
                        quantity_to_sell,
                        price=current_price,
                        market=market_type,
                    )
                    notification.send_notification(
                        f"🔻 [System 3 매도 권고] {stock_code}: 확신 점수 {score} 도달. 매도를 실행합니다. 이유: {reasoning}"
                    )
                    try:
                        purchase_price = stock_to_sell.get("average_price", current_price)
                        pnl_percent = (current_price / purchase_price - 1) * 100 if purchase_price else 0
                        llm_trade_data = {
                            "stock_code": stock_code,
                            "purchase_price": purchase_price,
                            "sell_price": current_price,
                            "quantity": quantity_to_sell,
                            "pnl_percent": pnl_percent,
                            "reasoning": reasoning,
                            "sell_reason": f"System decision sell (score {score} <= {sell_threshold})",
                            "market_type": market_type,
                            "stop_loss_type": "N/A",
                            "stop_loss_value": "N/A",
                            "target_price": "N/A",
                            "sell_deadline_date": "N/A",
                        }
                        rag_manager.generate_and_add_llm_insight(llm_trade_data)
                    except Exception as e:
                        log.warning(f"Failed to generate/send LLM insight after decision sell: {e}")

        news_ids_to_mark = [news["id"] for news in unprocessed_news]
        db.mark_news_as_processed(news_ids_to_mark)

    except Exception as e:
        log.critical(
            f"A critical error in {market_type} decision job: {e}", exc_info=True
        )
        llm_provider.provider.handle_api_error(e, 1)


def monitoring_job(
    kis_api: TradingInterface, llm_provider: LLMProvider, trade_monitor: TradeMonitor
):
    log.info(f"--- Running Monitoring & Emergency Job at {datetime.now()} ---")
    try:
        data_ingestor = DataIngestion(kis_api)
        emergency_manager = EmergencyManager(kis_api, data_ingestor, llm=llm_provider)
        emergency_manager.check_for_emergency()
        trade_monitor.check_and_execute_sells()
    except Exception as e:
        log.error(f"Error in monitoring job: {e}", exc_info=True)
        llm_provider.provider.handle_api_error(e, 1)


def daily_balance_report_job(kis_api: TradingInterface, trade_monitor: TradeMonitor):
    log.info("--- Running Daily Balance Report Job ---")
    try:
        baseline_assets_dict = load_previous_day_closing_assets()
        balance_info = kis_api.get_balance()
        report_message = reporting.format_balance_for_slack(
            balance_info, baseline_assets_dict
        )
        notification.send_notification(report_message)

        current_portfolio = balance_info.get("portfolio_kr", []) + balance_info.get(
            "portfolio_us", []
        )
        shared_trade_monitor.sync_with_account(current_portfolio)
    except Exception as e:
        log.error(f"Error in daily balance report job: {e}")


def market_risk_check_job(kis_api: TradingInterface, llm_provider: LLMProvider):
    """정기적으로 시장 위험(VIX 등)을 점검하고 Slack으로 간단한 리포트를 전송합니다."""
    log.info("--- Running Scheduled Market Risk Check Job ---")
    try:
        data_ingestor = DataIngestion(kis_api)
        market_condition_agent = MarketConditionAgent(llm_provider)
        assessment = market_condition_agent.analyze(
            data_ingestor.get_vix_index(),
            data_ingestor.get_market_index(),
            data_ingestor.get_general_market_news("general"),
        )
        msg = (
            f"📊 *정기 시장 위험 보고*\n"
            f"- 현재 VIX: *{data_ingestor.get_vix_index():.2f}*\n"
            f"- 동적 VIX 임계값: *{assessment.get('dynamic_vix_threshold', 35.0):.2f}*\n"
            f"- BUY 게이트: *{assessment.get('buy_conviction_threshold', 6.0):.2f}*\n"
            f"- SELL 게이트: *{assessment.get('sell_conviction_threshold', -6.0):.2f}*\n"
            f"- Reason: {assessment.get('reasoning', 'N/A')}"
        )
        notification.send_notification(msg)
    except Exception as e:
        log.error(f"Error in market risk check job: {e}", exc_info=True)
        try:
            llm_provider.provider.handle_api_error(e, 1)
        except Exception:
            pass


def monthly_performance_report_job(kis_api: TradingInterface):
    log.info("--- Running Monthly Performance Report Job ---")
    try:
        today = datetime.now()
        if today.day != 1:
            return
        balance_info = kis_api.get_balance()
        if not balance_info or "total_assets" not in balance_info:
            return
        total_assets = balance_info.get("total_assets", 0)
        metrics = compute_rolling_30d_metrics(total_assets)
        base_equity_30d = metrics.get("base_equity_30d", total_assets)
        pnl_30d_pct = metrics.get("pnl_30d_pct", 0.0)
        max_dd_30d_pct = metrics.get("max_drawdown_30d_pct", 0.0)
        if base_equity_30d <= 0:
            return
        pnl_krw = total_assets - base_equity_30d
        msg = (
            "📆 *월간 성과 요약 (롤링 30일 기준)*\n"
            f"- 기준 자본: ₩{base_equity_30d:,.0f}\n"
            f"- 현재 자본: ₩{total_assets:,.0f}\n"
            f"- 30일 수익: ₩{pnl_krw:,.0f} ({pnl_30d_pct:+.2f}%)\n"
            f"- 추정 최대 낙폭(MDD): {max_dd_30d_pct:+.2f}%\n"
            "- 목표 수익률: +20.00%\n"
        )
        notification.send_notification(msg)
    except Exception as e:
        log.error(f"Error in monthly performance report job: {e}", exc_info=True)


def deep_portfolio_review_job(
    kis_api: TradingInterface, llm_provider: LLMProvider, trade_monitor: TradeMonitor
):
    log.info("--- Running Deep Portfolio Review Job ---")
    
    log.info("슬랙으로 로그 파일을 전송합니다.")
    notification.send_log_file_to_slack()

    # ✨ 1. 현재 열려있는 시장을 확인합니다.
    active_market = None
    if is_kr_market_open():
        active_market = "KR"
        log.info("Korean market is open. Reviewing KR portfolio.")
    elif is_us_market_open():
        active_market = "US"
        log.info("US market is open. Reviewing US portfolio.")
    else:
        log.info("No markets are open for deep portfolio review. Skipping job.")
        return

    try:
        data_ingestor = DataIngestion(kis_api)

        # ✨ 2. 시장 상황을 분석하여 동적 매도 임계값을 설정합니다.
        market_condition_agent = MarketConditionAgent(llm_provider)
        market_conditions = market_condition_agent.analyze(
            data_ingestor.get_vix_index(),
            data_ingestor.get_market_index(),
            data_ingestor.get_general_market_news("general"),
        )
        sell_threshold = market_conditions.get(
            "sell_conviction_threshold", -6
        )  # 기본값 -6
        log.info(f"Dynamic sell threshold for deep review set to: {sell_threshold}")

        review_agent = PortfolioReviewAgent(llm_provider)
        portfolio = kis_api.get_portfolio()  # 최신 포트폴리오 정보 조회

        # ✨ 3. 현재 열린 시장에 해당하는 종목만 필터링합니다.
        trades_to_review = {
            code: info
            for code, info in trade_monitor.active_trades.items()
            if info.get("market_type") == active_market
        }

        if not trades_to_review:
            log.info(f"No active trades to review for the {active_market} market.")
            return

        analyses_to_add = []
        critical_events_to_add = []

        for stock_code, trade_info in trades_to_review.items():
            recent_fill_stats = trade_monitor.rag_manager.get_recent_fill_stats(days=3)
            rag_query = f"Reviewing current holding of {stock_code}. Initial reasoning was: {trade_info.get('reasoning')}"
            historical_analysis = trade_monitor.rag_manager.retrieve_relevant_analysis(
                stock_code, rag_query, n_results=3
            )
            relevant_news = trade_monitor.rag_manager.retrieve_relevant_news(
                stock_code, rag_query, n_results=5
            )

            current_analysis_data = {
                "ohlcv": data_ingestor.get_technical_data(stock_code, market=active_market),
                "fundamentals": data_ingestor.get_fundamental_data(stock_code),
                "current_price": kis_api.fetch_price(stock_code, market=active_market),
                "news": data_ingestor.get_company_news(
                    stock_code,
                    (datetime.now() - timedelta(days=7)).strftime("%Y-%m-%d"),
                    datetime.now().strftime("%Y-%m-%d"),
                )
            }
            initial_reasoning = trade_info.get("reasoning", "N/A")

            review_result = review_agent.review_holding(
                stock_code=stock_code,
                initial_reasoning=initial_reasoning,
                current_analysis=current_analysis_data,
                historical_analysis=historical_analysis,
                relevant_news=relevant_news,
                review_context="PERIODIC PORTFOLIO REVIEW",
                recent_fill_stats=recent_fill_stats,
            )

            if not review_result:
                continue
            
            analyses_to_add.append({"stock_code": stock_code, "report": json.dumps(review_result, default=str)})

            conviction_score = review_result.get("conviction_score")

            # ✨ 4. 확신 점수가 매도 임계값보다 낮으면 매도 실행
            if conviction_score is not None and conviction_score <= sell_threshold:
                stock_to_sell = next(
                    (s for s in portfolio if s["stock_code"] == stock_code), None
                )
                if stock_to_sell:
                    reason = f"Deep review sell: {review_result.get('recommendation_summary')}"
                    # RAG에 중요 이벤트를 기록합니다.
                    event_summary = (
                        f"CRITICAL ALERT ({datetime.now().strftime('%Y-%m-%d')}): {stock_code} was sold based on a deep portfolio review. "
                        f"Conviction Score: {conviction_score}. "
                        f"Reason: {review_result.get('recommendation_summary')}"
                    )
                    
                    critical_events_to_add.append({"stock_code": stock_code, "summary": event_summary})
                    
                    log.warning(
                        f"Executing SELL for {stock_code} based on review score ({conviction_score} <= {sell_threshold})."
                    )
                    reason = f"Deep review sell: {review_result.get('recommendation_summary')}"
                    kis_api.place_sell_order(
                        stock_code,
                        stock_to_sell["quantity"],
                        price=stock_to_sell["current_price"],
                        market=active_market,
                    )
                    trade_monitor.logger.log_trade(
                        stock_code,
                        "SELL",
                        stock_to_sell["quantity"],
                        stock_to_sell["current_price"],
                        reason,
                    )
                    notification.send_notification(
                        f"🔻 [Deep Portfolio Sell] {ticker_utils.format_for_slack(stock_code)}: Conviction Score reached {conviction_score}. \n> Reason: {reason}"
                    )
                    try:
                        current_price = stock_to_sell["current_price"]
                        quantity = stock_to_sell["quantity"]
                        purchase_price = trade_info.get("purchase_price")
                        pnl_percent = (current_price / purchase_price - 1) * 100 if purchase_price else 0
                        llm_trade_data = {
                            "stock_code": stock_code,
                            "purchase_price": purchase_price,
                            "sell_price": current_price,
                            "quantity": quantity,
                            "pnl_percent": pnl_percent,
                            "reasoning": trade_info.get("reasoning", "N/A"),
                            "sell_reason": reason,
                            "market_type": active_market,
                            "stop_loss_type": trade_info.get("stop_loss_type", "N/A"),
                            "stop_loss_value": trade_info.get("stop_loss_value", "N/A"),
                            "target_price": trade_info.get("target_price", "N/A"),
                            "sell_deadline_date": trade_info.get("sell_deadline_date", "N/A"),
                        }
                        trade_monitor.rag_manager.generate_and_add_llm_insight(llm_trade_data)
                    except Exception as e:
                        log.warning(f"Failed to generate/send LLM insight after deep review sell: {e}")
                    if stock_code in trade_monitor.active_trades:
                        del trade_monitor.active_trades[stock_code]
                        trade_monitor._save_state()
            else:
                # ✨ 5. 매도하지 않으면 전략 업데이트 시도 + Slack 알림 전송
                trade_monitor.update_trade_parameters(stock_code, review_result)
                try:
                    summary = review_result.get("recommendation_summary") or review_result.get("summary") or "No summary provided."
                    notification.send_notification(
                        f"📊 [Deep Portfolio Review] {ticker_utils.format_for_slack(stock_code)}: Conviction Score {conviction_score}.\n> Summary: {summary}"
                    )
                except Exception as e:
                    log.warning(f"Failed to send Slack notification for deep review (non-sell) {stock_code}: {e}")

        if analyses_to_add:
            trade_monitor.rag_manager.add_analyses_to_db(analyses_to_add)
        if critical_events_to_add:
            trade_monitor.rag_manager.add_critical_events(critical_events_to_add)

        log.info("포트폴리오 리뷰 완료.")

    except Exception as e:
        log.error(f"Error in deep portfolio review job: {e}", exc_info=True)
        llm_provider.provider.handle_api_error(e, 1)


def save_daily_closing_assets(kr_assets: float, us_assets: float):
    """하루 마감 시점의 국내 및 해외 자산을 파일에 저장합니다."""
    today_str = datetime.now().strftime("%Y-%m-%d")
    total_assets = kr_assets + us_assets
    data = {
        "date": today_str,
        "kr_closing_krw": kr_assets,
        "us_closing_krw": us_assets,
        "total_closing_krw": total_assets,
    }
    with open(BASELINE_FILE, "w") as f:
        json.dump(data, f, indent=4)
    print(
        f"저장 완료 ({today_str}): KR(₩{kr_assets:,.0f}), US(₩{us_assets:,.0f}), 총(₩{total_assets:,.0f})"
    )

    try:
        history = []
        if os.path.exists(ASSET_HISTORY_FILE):
            with open(ASSET_HISTORY_FILE, "r") as f:
                history = json.load(f)
        if not isinstance(history, list):
            history = []
        filtered = []
        for item in history:
            if item.get("date") != today_str:
                filtered.append(item)
        filtered.append({"date": today_str, "total_assets": total_assets})
        filtered.sort(key=lambda x: x.get("date", ""))
        if len(filtered) > 90:
            filtered = filtered[-90:]
        with open(ASSET_HISTORY_FILE, "w") as f:
            json.dump(filtered, f, indent=4)

        # KR 개별 자산 히스토리 업데이트
        history_kr = []
        if os.path.exists(ASSET_HISTORY_FILE_KR):
            with open(ASSET_HISTORY_FILE_KR, "r") as f:
                history_kr = json.load(f)
        if not isinstance(history_kr, list):
            history_kr = []
        filtered_kr = []
        for item in history_kr:
            if item.get("date") != today_str:
                filtered_kr.append(item)
        filtered_kr.append({"date": today_str, "total_assets": kr_assets})
        filtered_kr.sort(key=lambda x: x.get("date", ""))
        if len(filtered_kr) > 90:
            filtered_kr = filtered_kr[-90:]
        with open(ASSET_HISTORY_FILE_KR, "w") as f:
            json.dump(filtered_kr, f, indent=4)

        # US 개별 자산 히스토리 업데이트
        history_us = []
        if os.path.exists(ASSET_HISTORY_FILE_US):
            with open(ASSET_HISTORY_FILE_US, "r") as f:
                history_us = json.load(f)
        if not isinstance(history_us, list):
            history_us = []
        filtered_us = []
        for item in history_us:
            if item.get("date") != today_str:
                filtered_us.append(item)
        filtered_us.append({"date": today_str, "total_assets": us_assets})
        filtered_us.sort(key=lambda x: x.get("date", ""))
        if len(filtered_us) > 90:
            filtered_us = filtered_us[-90:]
        with open(ASSET_HISTORY_FILE_US, "w") as f:
            json.dump(filtered_us, f, indent=4)
    except Exception as e:
        log.warning(f"Failed to update asset history file: {e}")

def load_previous_day_closing_assets() -> dict:
    """전일 마감 자산 정보(국내/해외 분리)를 파일에서 불러옵니다."""
    try:
        with open(BASELINE_FILE, "r") as f:
            data = json.load(f)
            # 저장된 값을 딕셔너리 형태로 반환
            return {
                "kr": data.get("kr_closing_krw", 0),
                "us": data.get("us_closing_krw", 0),
                "total": data.get("total_closing_krw", 0),
            }
    except (FileNotFoundError, json.JSONDecodeError):
        # 파일이 없거나 비어있을 경우, 초기값을 반환
        return {"kr": 10000000, "us": 350000000, "total": 360000000}


def compute_rolling_30d_metrics(current_total_assets: float, history_file: str = ASSET_HISTORY_FILE) -> dict:
    today = datetime.now().date()
    base = current_total_assets
    pnl_pct = 0.0
    max_dd_pct = 0.0
    try:
        if os.path.exists(history_file):
            with open(history_file, "r") as f:
                history = json.load(f)
        else:
            history = []
    except Exception:
        history = []

    records = []
    for item in history:
        date_str = item.get("date")
        total = item.get("total_assets")
        try:
            d = datetime.strptime(date_str, "%Y-%m-%d").date() if date_str else None
        except Exception:
            d = None
        if d and isinstance(total, (int, float)):
            if d >= today - timedelta(days=30):
                records.append((d, float(total)))

    if not records and history:
        parsed = []
        for item in history:
            date_str = item.get("date")
            total = item.get("total_assets")
            try:
                d = datetime.strptime(date_str, "%Y-%m-%d").date() if date_str else None
            except Exception:
                d = None
            if d and isinstance(total, (int, float)):
                parsed.append((d, float(total)))
        parsed.sort(key=lambda x: x[0])
        records = parsed

    if records:
        records.sort(key=lambda x: x[0])
        base = records[0][1]
        if base > 0:
            pnl_pct = (current_total_assets - base) / base * 100.0
        peak = records[0][1]
        max_drawdown = 0.0
        for _, value in records:
            if value > peak:
                peak = value
            if peak > 0:
                dd = (value - peak) / peak * 100.0
                if dd < max_drawdown:
                    max_drawdown = dd
        max_dd_pct = max_drawdown

    return {
        "base_equity_30d": base,
        "pnl_30d_pct": round(pnl_pct, 4),
        "max_drawdown_30d_pct": round(max_dd_pct, 4),
    }

def run_daily_close_process(kis_api: TradingInterface):
    """하루 모든 장 마감 후, 최종 국내/해외 자산을 저장합니다."""
    log.info("일일 마감 프로세스를 시작합니다...")
    try:
        balance_info = kis_api.get_balance()
        if balance_info:
            # kis_api.get_balance()가 아래 키를 반환한다고 가정합니다.
            kr_assets = balance_info.get("kr_total_krw", 0)
            us_assets = balance_info.get("us_total_krw", 0)

            # (3) 분리된 자산 정보를 저장합니다.
            if kr_assets > 0 or us_assets > 0:
                save_daily_closing_assets(kr_assets=kr_assets, us_assets=us_assets)
            else:
                log.warning("마감 자산을 저장할 수 없습니다 (자산 없음).")
            
            portfolio_kr = balance_info.get("portfolio_kr") or []
            portfolio_us = balance_info.get("portfolio_us") or []
            current_portfolio = portfolio_kr + portfolio_us
            if isinstance(current_portfolio, list) and all(
                isinstance(item, dict) for item in current_portfolio
            ):
                shared_trade_monitor.sync_with_account(current_portfolio)
            else:
                log.warning(
                    f"Invalid portfolio format in daily close process, skipping sync. Received: {type(current_portfolio)}"
                )
        # --- LLM/Embedding 일일 사용량 및 예외 개수를 Slack으로 전송 ---
        try:
            usage_file = os.path.join(config.LOG_DIR, "api_key_usage.json")
            if os.path.exists(usage_file):
                with open(usage_file, "r") as f:
                    usage_state = json.load(f)
            else:
                usage_state = {}

            date_str = usage_state.get("date", "unknown")
            usage = usage_state.get("usage", {})

            # key별 LLM 호출 수 합산
            per_key_lines = []
            total_llm_calls = 0
            for key_alias, stats in usage.items():
                if key_alias == "_embedding_total":
                    continue
                llm_calls = int(stats.get("llm", 0))
                total_llm_calls += llm_calls
                per_key_lines.append(f"- {key_alias}: {llm_calls}회")

            # Embedding은 전역 합산 (_embedding_total 키 사용, 과거 포맷도 고려)
            embedding_total = 0
            if "_embedding_total" in usage:
                embedding_total = int(usage.get("_embedding_total", 0))
            else:
                # 과거 버전 호환: key별 embedding 합계를 한 번에 집계
                embedding_total = sum(int(v.get("embedding", 0)) for v in usage.values())

            per_key_block = "\n".join(per_key_lines) if per_key_lines else "(No LLM calls recorded)"

            # 하루 비-Quota 예외 개수 로드
            error_file = os.path.join(config.LOG_DIR, "daily_error_stats.json")
            error_state = {}
            if os.path.exists(error_file):
                try:
                    with open(error_file, "r") as f:
                        error_state = json.load(f)
                except Exception:
                    error_state = {}
            error_date = error_state.get("date", date_str)
            error_count = int(error_state.get("error_count", 0))

            usage_msg = (
                f"📊 *일일 LLM/Embedding 사용량 및 에러 보고* (기준일 {date_str})\n"
                f"- 총 LLM 호출 수: *{total_llm_calls}회*\n"
                f"- 총 Embedding 호출 수: *{embedding_total}회*\n"
                f"- 비-Quota 예외 발생 횟수: *{error_count}건* (기록일 {error_date})\n"
                f"- 키별 LLM 사용량:\n{per_key_block}"
            )

            notification.send_notification(usage_msg)
        except Exception as e:
            log.warning(f"Failed to send daily LLM/Embedding usage report: {e}")
    except Exception as e:
        log.error(f"일일 마감 프로세스 중 오류 발생: {e}")


# 대기 중인 긴급 매도 주문 처리
def process_pending_sells_job(kis_api: TradingInterface, trade_monitor: TradeMonitor):
    log.info("--- Running Pending Emergency Sells Job ---")
    if not os.path.exists(PENDING_SELLS_FILE):
        return

    try:
        with open(PENDING_SELLS_FILE, "r") as f:
            pending_sells = json.load(f)
    except (json.JSONDecodeError, FileNotFoundError):
        return

    remaining_sells = []
    portfolio = kis_api.get_portfolio()
    portfolio_dict = {s["stock_code"]: s for s in portfolio}

    for sell_order in pending_sells:
        stock_code = sell_order["stock_code"]
        market_type = sell_order["market_type"]

        is_market_open = (market_type == "US" and is_us_market_open()) or (
            market_type == "KR" and is_kr_market_open()
        )

        if is_market_open:
            if stock_code in portfolio_dict:
                log.info(f"Executing pending emergency sell for {stock_code}.")
                stock_info = portfolio_dict[stock_code]
                order_result = kis_api.place_sell_order(
                    stock_code,
                    stock_info["quantity"],
                    price=stock_info["current_price"],
                    market=market_type,
                )
                if order_result:
                    reason = f"Pending emergency sell executed: {sell_order['reason']}"
                    trade_monitor.logger.log_trade(
                        stock_code,
                        "SELL",
                        stock_info["quantity"],
                        stock_info["current_price"],
                        reason,
                    )
                    notification.send_notification(
                        f"🚨 [긴급 매도 실행] {ticker_utils.format_for_slack(stock_code)} 장 개시. 대기 중이던 매도를 실행했습니다."
                    )
                    try:
                        trade_info = trade_monitor.active_trades.get(stock_code, {})
                        current_price = stock_info["current_price"]
                        quantity = stock_info["quantity"]
                        purchase_price = trade_info.get("purchase_price") or stock_info.get("average_price")
                        pnl_percent = (current_price / purchase_price - 1) * 100 if purchase_price else 0
                        llm_trade_data = {
                            "stock_code": stock_code,
                            "purchase_price": purchase_price,
                            "sell_price": current_price,
                            "quantity": quantity,
                            "pnl_percent": pnl_percent,
                            "reasoning": trade_info.get("reasoning", "N/A"),
                            "sell_reason": reason,
                            "market_type": sell_order.get("market_type", "N/A"),
                            "stop_loss_type": trade_info.get("stop_loss_type", "N/A"),
                            "stop_loss_value": trade_info.get("stop_loss_value", "N/A"),
                            "target_price": trade_info.get("target_price", "N/A"),
                            "sell_deadline_date": trade_info.get("sell_deadline_date", "N/A"),
                        }
                        trade_monitor.rag_manager.generate_and_add_llm_insight(llm_trade_data)
                    except Exception as e:
                        log.warning(f"Failed to generate/send LLM insight after pending emergency sell: {e}")
                    if stock_code in trade_monitor.active_trades:
                        del trade_monitor.active_trades[stock_code]
                else:
                    remaining_sells.append(sell_order)  # 실패 시 큐에 남김
            else:
                log.warning(
                    f"Pending sell for {stock_code} skipped as it's no longer in the portfolio."
                )
        else:
            remaining_sells.append(sell_order)  # 아직 장이 열리지 않았으면 큐에 남김

    with open(PENDING_SELLS_FILE, "w") as f:
        json.dump(remaining_sells, f, indent=4)
    trade_monitor._save_state()


if __name__ == "__main__":
    log.info(f"Initializing System in {config.RUN_MODE} mode...")
    
    ticker_utils.init_ticker_map()

    if not config.GOOGLE_API_KEYS:
        raise ValueError(
            "No Google API keys found. Please check GOOGLE_API_KEY_1, _2, ... in api.env file."
        )

    shared_kis_api = TradingInterface(mock_trading=config.MOCK_TRADING)
    shared_llm_provider = LLMProvider(api_keys=config.GOOGLE_API_KEYS)
    shared_db = NewsDatabase()

    if config.RUN_MODE == "TRADE":
        shared_rag_manager = RAGManager(embeddings=runnable_llm_provider, llm_provider=runnable_llm_provider)
        shared_trade_monitor = TradeMonitor(
            shared_kis_api, logger.TradeLogger(), shared_rag_manager
        )
        
        # 1. get_balance()를 호출하여 전체 계좌 정보를 가져옵니다.
        balance_info = shared_kis_api.get_balance()

        # 2. get_balance() 결과가 유효한지 확인합니다.
        if balance_info and "portfolio_kr" in balance_info:
            current_portfolio = balance_info.get("portfolio_kr", []) + balance_info.get(
                "portfolio_us", []
            )

            # 데이터 형식 검증
            is_valid_portfolio = isinstance(current_portfolio, list) and all(
                isinstance(item, dict) for item in current_portfolio
            )

            if is_valid_portfolio:
                # 3. 유효한 경우에만 동기화 함수를 실행합니다.
                baseline_assets_dict = load_previous_day_closing_assets()
                shared_trade_monitor.sync_with_account(current_portfolio)
                report_message = reporting.format_balance_for_slack(balance_info)
                notification.send_notification(report_message, baseline_assets_dict)
            else:
                # 데이터 형식이 올바르지 않으면 에러를 발생시켜 프로그램을 중단시킵니다.
                raise TypeError(
                    f"포트폴리오 데이터 형식이 올바르지 않아 동기화를 중단합니다. 수신된 데이터: {current_portfolio}"
                )
        else:
            # 계좌 정보를 가져올 수 없으면 에러를 발생시켜 프로그램을 중단시킵니다.
            raise ConnectionError("계좌 정보를 가져올 수 없어 동기화를 중단합니다.")

        news_job = partial(news_collection_job, shared_kis_api, shared_db)
        kr_job = partial(
            kr_decision_making_job,
            shared_kis_api,
            runnable_llm_provider,
            shared_rag_manager,
            shared_trade_monitor,
            shared_db,
        )
        us_job = partial(
            us_decision_making_job,
            shared_kis_api,
            runnable_llm_provider,
            shared_rag_manager,
            shared_trade_monitor,
            shared_db,
        )
        mon_job = partial(
            monitoring_job, shared_kis_api, runnable_llm_provider, shared_trade_monitor
        )
        bal_job = partial(
            daily_balance_report_job, shared_kis_api, shared_trade_monitor
        )
        risk_job = partial(market_risk_check_job, shared_kis_api, runnable_llm_provider)
        monthly_job = partial(
            monthly_performance_report_job, shared_kis_api
        )
        deep_review_job = partial(
            deep_portfolio_review_job,
            shared_kis_api,
            runnable_llm_provider,
            shared_trade_monitor,
        )
        pending_sell_job = partial(
            process_pending_sells_job, shared_kis_api, shared_trade_monitor
        )
        close_job = partial(run_daily_close_process, shared_kis_api)

        news_job_handle = schedule.every(10).minutes.do(news_job)
        kr_job_handle = schedule.every(15).minutes.do(kr_job)
        us_job_handle = schedule.every(15).minutes.do(us_job)
        mon_job_handle = schedule.every(15).minutes.do(mon_job)
        deep_review_job_handle = schedule.every(15).minutes.do(deep_review_job)

        # 15분 주기 내에서 작업 시작 시점을 분산합니다.
        # 기준 시각(0분, 15분, ...)에는 KR/US 결정 잡을 실행하고,
        # +5분에는 모니터링 잡, +10분에는 딥 리뷰 잡이 실행되도록 next_run을 조정합니다.
        base_time = kr_job_handle.next_run
        us_job_handle.next_run = base_time
        mon_job_handle.next_run = base_time + timedelta(minutes=5)
        deep_review_job_handle.next_run = base_time + timedelta(minutes=10)

        # schedule.every().day.at("22:35").do(deep_review_job)  # 미장 시작
        # schedule.every().day.at("02:00").do(deep_review_job)  # 미장 도중
        # schedule.every().day.at("09:05").do(deep_review_job)  # 국장 시작
        # schedule.every().day.at("12:00").do(deep_review_job)  # 국장 도중
        schedule.every().day.at("08:50").do(bal_job)  # 국장 10분전
        schedule.every().day.at("22:20").do(bal_job)  # 미장 10분전
        schedule.every().day.at("16:00").do(risk_job)
        schedule.every().day.at("06:00").do(risk_job)
        schedule.every().day.at("16:06").do(bal_job)  # 국장 5분뒤
        schedule.every().day.at("06:05").do(bal_job)  # 미장 5분뒤
        schedule.every().day.at("09:00").do(monthly_job)

        schedule.every().day.at("09:03").do(pending_sell_job)
        schedule.every().day.at("22:33").do(pending_sell_job)

        schedule.every().day.at("06:10").do(close_job)  # 미장 마감 후

        log.info("Scheduler started with separated News Collector.")
        notification.send_notification("Trading Bot Started.")

        news_job()
        kr_job()
        us_job()

        # 1. 다음 XX:01:00가 될 때까지 대기
        log.info("Waiting for the next XX:X1 to start the first run.")
        while True:
            now = datetime.now()
            if now.minute % 15 == 1 and now.second == 0:
                log.info(
                    f"It's {now.strftime('%H:%M:%S')}. Starting the scheduler loop."
                )
                break
            time.sleep(0.5)  # 0.5초마다 체크하여 정확도 높임

        # 2. 메인 스케줄러 루프 시작
        while True:
            try:
                schedule.run_pending()
                shared_trade_monitor.flush_rag_updates()
                # 다음 정각 분까지 대기하여 루프 시간 맞춤
                now = datetime.now()
                seconds_to_wait = 60 - now.second
                time.sleep(seconds_to_wait if seconds_to_wait > 0 else 60)
            except Exception as e:
                log.info(e)

    elif config.RUN_MODE == "BACKTEST":
        from src.core import LLMStrategyBacktester

        log.info("--- STARTING BACKTEST MODE ---")
        backtester = LLMStrategyBacktester(
            start_date_str="2023-06-01",
            end_date_str="2023-06-30",
            initial_capital=10000000,
            kis_api=shared_kis_api,
            llm=runnable_llm_provider,
            embeddings=runnable_llm_provider,
        )
        watchlist = ["AAPL", "MSFT", "GOOGL", "AMZN", "NVDA"]
        backtester.run(watchlist)
        log.info("--- BACKTEST MODE FINISHED ---")