import json
import os
import time
from datetime import datetime, timedelta
from functools import partial

import pandas as pd
import schedule

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
from src.services.database_manager import NewsDatabase
from src.utils import logger, notification, reporting
from src.utils.api_key_manager import LLMProvider, runnable_llm_provider
from src.utils.market_utils import is_kr_market_open, is_us_market_open

# 봇의 모든 로깅을 관리하는 로거 인스턴스를 생성합니다.
log = logger.get_logger(__name__)

BASELINE_FILE = "asset_baseline.json"


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

        market_condition_agent = MarketConditionAgent(llm_provider)
        market_conditions = market_condition_agent.analyze(
            data_ingestor.get_vix_index(),
            data_ingestor.get_market_index(),
            unprocessed_news,
        )

        buy_threshold = market_conditions.get("buy_conviction_threshold", 6)
        sell_threshold = market_conditions.get("sell_conviction_threshold", -6)

        news_screener = NewsScreenerAgent(llm_provider)
        watchlist_dict = news_screener.generate_watchlist_from_news(unprocessed_news)

        watchlist = watchlist_dict.get(market_type.lower(), [])

        if not watchlist:
            log.info(
                f"No relevant {market_type} stocks found from news. Skipping analysis."
            )
            news_ids_to_mark = [news["id"] for news in unprocessed_news]
            db.mark_news_as_processed(news_ids_to_mark)
            return

        tools = StockAnalysisTools(data_ingestor, llm=llm_provider).get_tools()
        orchestrator = TradingOrchestrator(rag_manager, llm_provider, tools)

        all_stocks_data = {
            stock: {
                "ohlcv": data_ingestor.get_technical_data(stock, market=market_type),
                "news": data_ingestor.get_company_news(
                    stock,
                    (datetime.now() - timedelta(days=365)).strftime("%Y-%m-%d"),
                    datetime.now().strftime("%Y-%m-%d"),
                ),
                "fundamentals": data_ingestor.get_fundamental_data(stock),
            }
            for stock in watchlist
            if stock not in trade_monitor.active_trades
        }

        if not all_stocks_data:
            log.info("No new stocks to analyze.")
            news_ids_to_mark = [news["id"] for news in unprocessed_news]
            db.mark_news_as_processed(news_ids_to_mark)
            return

        balance_info = kis_api.get_balance(market_type=market_type)

        # get_balance()가 최종 실패 시 빈 딕셔너리를 반환하므로, 이를 확인하고 작업을 중단합니다.
        if not balance_info or "total_assets" not in balance_info:
            log.error("계좌 잔고 조회에 최종 실패하여 이번 작업을 중단합니다.")
            return

        daily_asset_baseline = load_previous_day_closing_assets()
        report_message = reporting.format_balance_for_slack(
            balance_info, daily_asset_baseline
        )
        notification.send_notification(report_message)

        final_decisions = orchestrator.get_batch_trade_decisions(
            all_stocks_data, balance_info
        )

        for decision in final_decisions:
            score = decision.get("conviction_score", 0)
            stock_code = decision.get("stock_code")
            reasoning = decision.get("reasoning", "No reasoning provided.")

            if score >= buy_threshold:
                price_data = all_stocks_data.get(stock_code, {}).get("ohlcv")
                if price_data is not None and not price_data.empty:
                    current_price = price_data.iloc[-1]["Close"]

                    if config.MOCK_TRADING and market_type == "US":
                        exchange_rate = kis_api.get_exchange_rate("USD", "KRW")
                        balance_info["cash_balance"] = (
                            balance_info["cash_balance"] / exchange_rate
                        )

                    investment_amount = min(
                        balance_info.get("cash_balance", 0) * 0.1,
                        config.USER_RULES.get("max_investment_per_stock", 1000000),
                    )
                    quantity = (
                        int(investment_amount / current_price)
                        if current_price > 0
                        else 0
                    )

                    if quantity > 0:
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
                            msg = f"✅ [System 3 매수] {stock_code}: {quantity}주 @ {'$' if market_type == 'US' else '₩'}{current_price:,.2f}"
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
        log.error(f"Error in daily balance report job: {e}", exc_info=True)


def market_risk_check_job(kis_api: TradingInterface, llm_provider: LLMProvider):
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
            f"📊 *정기 시장 위험 보고*\n- 현재 VIX: *{data_ingestor.get_vix_index():.2f}*\n"
            f"- 동적 위험 임계값: *{assessment.get('dynamic_vix_threshold', 35.0):.2f}*"
        )
        notification.send_notification(msg)
    except Exception as e:
        log.error(f"Error in market risk check job: {e}", exc_info=True)
        llm_provider.provider.handle_api_error(e, 1)


def deep_portfolio_review_job(
    kis_api: TradingInterface, llm_provider: LLMProvider, trade_monitor: TradeMonitor
):
    log.info("--- Running Deep Portfolio Review Job ---")

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

        for stock_code, trade_info in trades_to_review.items():
            rag_query = f"Reviewing current holding of {stock_code}. Initial reasoning was: {trade_info.get('reasoning')}"
            historical_analysis = trade_monitor.rag_manager.retrieve_relevant_analysis(
                stock_code, rag_query, n_results=3
            )
            relevant_news = trade_monitor.rag_manager.retrieve_relevant_news(
                stock_code, rag_query, n_results=5
            )

            current_analysis_data = {
                "news": data_ingestor.get_company_news(
                    stock_code,
                    (datetime.now() - timedelta(days=7)).strftime("%Y-%m-%d"),
                    datetime.now().strftime("%Y-%m-%d"),
                )
            }
            initial_reasoning = trade_info.get("reasoning", "N/A")

            review_result = review_agent.review_holding(
                stock_code,
                initial_reasoning,
                current_analysis_data,
                historical_analysis,
                relevant_news,
            )

            if not review_result:
                continue

            conviction_score = review_result.get("conviction_score")

            # ✨ 4. 확신 점수가 매도 임계값보다 낮으면 매도 실행
            if conviction_score is not None and conviction_score <= sell_threshold:
                stock_to_sell = next(
                    (s for s in portfolio if s["stock_code"] == stock_code), None
                )
                if stock_to_sell:
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
                        f"🔻 [심층분석 매도] {stock_code}: 확신 점수 {conviction_score} 도달. \n> 이유: {reason}"
                    )
                    if stock_code in trade_monitor.active_trades:
                        del trade_monitor.active_trades[stock_code]
                        trade_monitor._save_state()
            else:
                # ✨ 5. 매도하지 않으면 전략 업데이트 시도
                trade_monitor.update_trade_parameters(stock_code, review_result)

        log.info("포트폴리오 리뷰 완료. 슬랙으로 로그 파일을 전송합니다.")
        notification.send_log_file_to_slack()

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
                        f"🚨 [긴급 매도 실행] {stock_code} 장 개시. 대기 중이던 매도를 실행했습니다."
                    )
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

    if not config.GOOGLE_API_KEYS:
        raise ValueError(
            "No Google API keys found. Please check GOOGLE_API_KEY_1, _2, ... in api.env file."
        )

    shared_kis_api = TradingInterface(mock_trading=config.MOCK_TRADING)
    shared_llm_provider = LLMProvider(api_keys=config.GOOGLE_API_KEYS)
    shared_db = NewsDatabase()

    if config.RUN_MODE == "TRADE":
        shared_rag_manager = RAGManager(runnable_llm_provider)
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

        schedule.every(10).minutes.do(news_job)
        schedule.every(15).minutes.do(kr_job)
        schedule.every(15).minutes.do(us_job)
        schedule.every(15).minutes.do(mon_job)

        schedule.every().day.at("22:35").do(deep_review_job)  # 미장 시작
        schedule.every().day.at("02:00").do(deep_review_job)  # 미장 도중
        schedule.every().day.at("09:05").do(deep_review_job)  # 국장 시작
        schedule.every().day.at("12:00").do(deep_review_job)  # 국장 도중
        schedule.every().day.at("08:50").do(bal_job)  # 국장 10분전
        schedule.every().day.at("22:20").do(bal_job)  # 미장 10분전
        schedule.every().day.at("16:00").do(risk_job)
        schedule.every().day.at("06:00").do(risk_job)

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
            schedule.run_pending()
            # 다음 정각 분까지 대기하여 루프 시간 맞춤
            now = datetime.now()
            seconds_to_wait = 60 - now.second
            time.sleep(seconds_to_wait if seconds_to_wait > 0 else 60)

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
