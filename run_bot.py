import os
import time
from datetime import datetime, timedelta
import pandas as pd
import schedule
from functools import partial
from langchain_google_genai import ChatGoogleGenerativeAI, GoogleGenerativeAIEmbeddings

from src import config
from src.utils import logger, notification, reporting
from src.utils.market_utils import is_us_market_open, is_kr_market_open
from src.utils.api_key_manager import LLMKeyRing
from src.data_providers import TradingInterface, DataIngestion
from src.orchestrators import TradingOrchestrator
from src.services import RAGManager
from src.services.database_manager import NewsDatabase
from src.core import TradeMonitor, LLMStrategyBacktester
from src.agents.market import NewsScreenerAgent, MarketConditionAgent, PortfolioReviewAgent
from src.agents.tools import StockAnalysisTools
from src.emergency import EmergencyManager

# 봇의 모든 로깅을 관리하는 로거 인스턴스를 생성합니다.
log = logger.get_logger(__name__)

# 오늘 날짜에 각 시장별 리포트를 보냈는지 추적하는 상태 변수
daily_report_sent = {"KR": None, "US": None}
# ✨ 오늘의 기준 자산을 기록하는 상태 변수 (시장별로 관리)
daily_asset_baseline = {"KR": None, "US": None}

def news_collection_job(kis_api: TradingInterface, db: NewsDatabase):
    """
    24시간 내내 주기적으로 시장 뉴스를 수집하여 데이터베이스에 저장합니다.
    (Finnhub 일반 뉴스 + Naver 종목 뉴스)
    """
    log.info("--- Running News Collection Job ---")
    data_ingestor = DataIngestion(kis_api)
    try:
        # Finnhub에서 일반 시장 뉴스 수집
        market_news = data_ingestor.get_general_market_news('general')
        if market_news:
            db.save_news(market_news)
    except Exception as e:
        # Finnhub API 오류가 발생하더라도, 프로그램을 중단하지 않고 경고만 기록합니다.
        log.warning(f"Could not fetch general market news from Finnhub due to an API error: {e}")

    # 2. Naver에서 KOSPI 종목 뉴스 샘플 수집
    try:
        kospi_tickers_path = os.path.join(config.DATA_DIR, 'kospi_tickers.csv')
        if os.path.exists(kospi_tickers_path):
            kospi_df = pd.read_csv(kospi_tickers_path)
            sample_size = min(10, len(kospi_df))
            sampled_stocks = kospi_df.sample(n=sample_size)
            
            for _, row in sampled_stocks.iterrows():
                stock_name = row['종목명']
                naver_news = data_ingestor.get_news_from_naver(stock_name)
                if naver_news:
                    db.save_news(naver_news)
                time.sleep(0.5)
        else:
            log.warning("KOSPI 종목 리스트 파일이 없어 Naver 뉴스 수집을 건너뜁니다.")
    except Exception as e:
        log.error(f"Error in Naver news collection part of the job: {e}", exc_info=True)

# --- 각 시장별 job 함수 정의 ---
def kr_decision_making_job(kis_api, key_ring: LLMKeyRing, rag_manager, trade_monitor, db):
    """한국 주식 시장에 대한 의사결정 프로세스를 실행합니다."""
    run_decision_process('KR', kis_api, key_ring, rag_manager, trade_monitor, db)

def us_decision_making_job(kis_api, key_ring: LLMKeyRing, rag_manager, trade_monitor, db):
    """미국 주식 시장에 대한 의사결정 프로세스를 실행합니다."""
    run_decision_process('US', kis_api, key_ring, rag_manager, trade_monitor, db)

def run_decision_process(market_type: str, kis_api: TradingInterface, key_ring: LLMKeyRing, rag_manager: RAGManager, trade_monitor: TradeMonitor, db: NewsDatabase):
    """
    지정된 시장(US 또는 KR)에 대한 전체 의사결정 프로세스를 실행하고, API 오류 발생 시 키 전환을 시도합니다.
    """
    if (market_type == 'US' and not is_us_market_open()) or \
       (market_type == 'KR' and not is_kr_market_open()):
        log.info(f"Decision job for {market_type} market skipped: Market closed.")
        return
    
    global daily_report_sent, daily_asset_baseline
    
    today_str = datetime.now().strftime('%Y-%m-%d')

    if daily_report_sent.get(market_type) != today_str:
        try:
            log.info(f"{market_type} 시장의 첫 분석 시작 전, 일일 계좌 현황을 보고합니다.")
            balance_info = kis_api.get_balance()
            if balance_info:
                # 오늘의 첫 자산 총액을 기준점으로 기록
                if daily_asset_baseline.get(market_type) is None:
                    daily_asset_baseline[market_type] = balance_info.get('total_assets')
                
                # 기준점을 format 함수에 함께 전달
                report_message = reporting.format_balance_for_slack(balance_info, daily_asset_baseline[market_type])
                notification.send_notification(report_message)
                daily_report_sent[market_type] = today_str
        except Exception as e:
            log.error(f"일일 계좌 현황 보고 중 오류 발생: {e}")

    # 1. 사용 가능한 API 키를 받아와서 LLM 객체를 생성합니다.
    active_key = key_ring.get_active_key()
    if not active_key:
        log.error(f"Decision job for {market_type} market skipped: No available API key.")
        return
    active_llm = ChatGoogleGenerativeAI(model="gemini-2.5-flash", temperature=0.2, google_api_key=active_key)

    job_start_time = datetime.now()
    log.info(f"\n--- Running Decision Job for {market_type} Market at {job_start_time.strftime('%Y-%m-%d %H:%M:%S')} ---")
    
    try:
        unprocessed_news = db.get_unprocessed_news()
        if not unprocessed_news:
            log.info("No new unprocessed news to analyze. Skipping decision job.")
            return
            
        notification.send_notification(f"🚀 [System 3] {market_type} 시장 분석 시작 ({len(unprocessed_news)}개 신규 뉴스)")
        
        data_ingestor = DataIngestion(kis_api)
        
        market_condition_agent = MarketConditionAgent(active_llm)
        market_conditions = market_condition_agent.analyze(
            data_ingestor.get_vix_index(),
            data_ingestor.get_market_index(),
            unprocessed_news
        )
        key_ring.increment_usage() # API 호출 1회 사용 기록
        
        buy_threshold = market_conditions.get("buy_conviction_threshold", 6)
        sell_threshold = market_conditions.get("sell_conviction_threshold", -6)

        news_screener = NewsScreenerAgent(active_llm)
        watchlist_dict = news_screener.generate_watchlist_from_news(unprocessed_news)
        key_ring.increment_usage() # API 호출 1회 사용 기록
        
        watchlist = watchlist_dict.get(market_type.lower(), [])
        
        if not watchlist:
            log.info(f"No relevant {market_type} stocks found from news. Skipping analysis.")
            news_ids_to_mark = [news['id'] for news in unprocessed_news]
            db.mark_news_as_processed(news_ids_to_mark)
            return

        tools = StockAnalysisTools(data_ingestor, llm=active_llm).get_tools()
        orchestrator = TradingOrchestrator(rag_manager, active_llm, tools)
        
        all_stocks_data = {
            stock: {
                "ohlcv": data_ingestor.get_technical_data(stock, market=market_type),
                "news": data_ingestor.get_company_news(stock, (datetime.now() - timedelta(days=365)).strftime('%Y-%m-%d'), datetime.now().strftime('%Y-%m-%d')),
                "fundamentals": data_ingestor.get_fundamental_data(stock)
            } for stock in watchlist if stock not in trade_monitor.active_trades
        }

        if not all_stocks_data:
            log.info("No new stocks to analyze.")
            news_ids_to_mark = [news['id'] for news in unprocessed_news]
            db.mark_news_as_processed(news_ids_to_mark)
            return

        balance_info = kis_api.get_balance(market_type=market_type)

        # get_balance()가 최종 실패 시 빈 딕셔너리를 반환하므로, 이를 확인하고 작업을 중단합니다.
        if not balance_info or 'total_assets' not in balance_info:
            log.error("계좌 잔고 조회에 최종 실패하여 이번 작업을 중단합니다.")
            return
        
        if daily_asset_baseline.get(today_str, {}).get(market_type) is None:
            if daily_asset_baseline.get(today_str) is None:
                daily_asset_baseline[today_str] = {}
            daily_asset_baseline[today_str][market_type] = balance_info.get('total_assets')
        
        report_message = reporting.format_balance_for_slack(balance_info, daily_asset_baseline[market_type])
        notification.send_notification(report_message)
        
        final_decisions = orchestrator.get_batch_trade_decisions(all_stocks_data, balance_info)
        key_ring.increment_usage() # API 호출 1회 사용 기록
        
        for decision in final_decisions:
            score = decision.get("conviction_score", 0)
            stock_code = decision.get("stock_code")
            reasoning = decision.get("reasoning", "No reasoning provided.")
            
            if score >= buy_threshold:
                price_data = all_stocks_data.get(stock_code, {}).get("ohlcv")
                if price_data is not None and not price_data.empty:
                    current_price = price_data.iloc[-1]['Close']
                    
                    if config.MOCK_TRADING and market_type == "US":        
                        exchange_rate = kis_api.get_exchange_rate("USD", "KRW")
                        balance_info['cash_balance'] = balance_info['cash_balance'] / exchange_rate
                    
                    investment_amount = min(
                        balance_info.get("cash_balance", 0) * 0.1,
                        config.USER_RULES.get("max_investment_per_stock", 1000000)
                    )
                    quantity = int(investment_amount / current_price) if current_price > 0 else 0

                    if quantity > 0:
                        log.info(f"Executing BUY for {quantity} shares of {stock_code} based on conviction score ({score} >= {buy_threshold}).")
                        order_result = kis_api.place_buy_order(stock_code, quantity, price=current_price, market=market_type)
                        if order_result:
                            msg = f"✅ [System 3 매수] {stock_code}: {quantity}주 @ {'$' if market_type == 'US' else '₩'}{current_price:,.2f}"
                            trade_monitor.logger.log_trade(stock_code, "BUY", quantity, current_price, reasoning)
                            trade_monitor.register_trade(
                                stock_code=stock_code, purchase_price=current_price, quantity=quantity,
                                target_gain_percentage=decision.get('target_gain_percentage', config.USER_RULES["target_profit_percent_per_trade"]),
                                stop_loss_value=decision.get('stop_loss_percentage', config.USER_RULES["max_loss_percent_per_trade"]),
                                sell_deadline_date=decision.get('sell_deadline_date', (datetime.now() + timedelta(days=7)).strftime('%Y-%m-%d')),
                                reasoning=reasoning,
                                market_type=market_type
                            )
                            notification.send_notification(msg)
            
            elif score <= sell_threshold:
                stock_to_sell = next((s for s in balance_info.get("portfolio", []) if s['stock_code'] == stock_code), None)
                if stock_to_sell:
                    log.info(f"Executing SELL for {stock_code} based on conviction score ({score} <= {sell_threshold}).")
                    current_price = stock_to_sell['current_price']
                    quantity_to_sell = stock_to_sell['quantity']
                    kis_api.place_sell_order(stock_code, quantity_to_sell, price=current_price, market=market_type)
                    notification.send_notification(f"🔻 [System 3 매도 권고] {stock_code}: 확신 점수 {score} 도달. 매도를 실행합니다. 이유: {reasoning}")
        
        news_ids_to_mark = [news['id'] for news in unprocessed_news]
        db.mark_news_as_processed(news_ids_to_mark)

    except Exception as e:
        log.critical(f"A critical error in {market_type} decision job: {e}", exc_info=True)
        key_ring.handle_api_error(e)

def monitoring_job(kis_api: TradingInterface, key_ring: LLMKeyRing, trade_monitor: TradeMonitor):
    active_key = key_ring.get_active_key()
    if not active_key:
        log.error("Monitoring job skipped: No available API key.")
        return
    active_llm = ChatGoogleGenerativeAI(model="gemini-2.5-flash", temperature=0.2, google_api_key=active_key)
    
    log.info(f"--- Running Monitoring & Emergency Job at {datetime.now()} ---")
    try:
        data_ingestor = DataIngestion(kis_api)
        emergency_manager = EmergencyManager(kis_api, data_ingestor, llm=active_llm)
        emergency_manager.check_for_emergency()
        key_ring.increment_usage()
        trade_monitor.check_and_execute_sells()
    except Exception as e:
        log.error(f"Error in monitoring job: {e}", exc_info=True)
        key_ring.handle_api_error(e)

def daily_balance_report_job(kis_api: TradingInterface):
    log.info("--- Running Daily Balance Report Job ---")
    try:
        balance_info = kis_api.get_balance()
        report_message = reporting.format_balance_for_slack(balance_info)
        notification.send_notification(report_message)
    except Exception as e:
        log.error(f"Error in daily balance report job: {e}", exc_info=True)

def market_risk_check_job(kis_api: TradingInterface, key_ring: LLMKeyRing):
    active_key = key_ring.get_active_key()
    if not active_key:
        log.error("Market risk check job skipped: No available API key.")
        return
    active_llm = ChatGoogleGenerativeAI(model="gemini-2.5-flash", temperature=0.2, google_api_key=active_key)

    log.info("--- Running Scheduled Market Risk Check Job ---")
    try:
        data_ingestor = DataIngestion(kis_api)
        market_condition_agent = MarketConditionAgent(active_llm)
        assessment = market_condition_agent.analyze(
            data_ingestor.get_vix_index(),
            data_ingestor.get_market_index(),
            data_ingestor.get_general_market_news('general')
        )
        key_ring.increment_usage()
        msg = (f"📊 *정기 시장 위험 보고*\n- 현재 VIX: *{data_ingestor.get_vix_index():.2f}*\n"
               f"- 동적 위험 임계값: *{assessment.get('dynamic_vix_threshold', 35.0):.2f}*")
        notification.send_notification(msg)
    except Exception as e:
        log.error(f"Error in market risk check job: {e}", exc_info=True)
        key_ring.handle_api_error(e)

def deep_portfolio_review_job(kis_api: TradingInterface, key_ring: LLMKeyRing, trade_monitor: TradeMonitor):
    active_key = key_ring.get_active_key()
    if not active_key:
        log.error("Deep portfolio review job skipped: No available API key.")
        return
    active_llm = ChatGoogleGenerativeAI(model="gemini-2.5-flash", temperature=0.2, google_api_key=active_key)

    log.info("--- Running Deep Portfolio Review Job ---")
    try:
        if not trade_monitor.active_trades:
            log.info("No active trades to review.")
            return
        
        data_ingestor = DataIngestion(kis_api)
        review_agent = PortfolioReviewAgent(active_llm)
        all_review_results = []

        for stock_code, trade_info in trade_monitor.active_trades.items():
            current_analysis_data = {
                "ohlcv": data_ingestor.get_technical_data(stock_code, market='US'),
                "news": data_ingestor.get_company_news(stock_code, (datetime.now() - timedelta(days=365)).strftime('%Y-%m-%d'), datetime.now().strftime('%Y-%m-%d')),
                "fundamentals": data_ingestor.get_fundamental_data(stock_code)
            }
            initial_reasoning = trade_info.get("reasoning", "Initial Buy based on system analysis.")
            review_result = review_agent.review_holding(stock_code, initial_reasoning, current_analysis_data)
            key_ring.increment_usage()
            
            if review_result and review_result.get("recommendation") in ["CONSIDER SELLING", "IMMEDIATE SELL"]:
                all_review_results.append(review_result)

        if all_review_results:
            report_message = reporting.format_review_for_slack(all_review_results)
            notification.send_notification(report_message)
        else:
            log.info("Deep portfolio review complete. No immediate actions recommended.")
            notification.send_notification("✅ *포트폴리오 심층 재평가 완료*: 모든 보유 포지션이 유효합니다.")
        
        log.info("포트폴리오 리뷰 완료. 슬랙으로 로그 파일을 전송합니다.")
        notification.send_log_file_to_slack()

    except Exception as e:
        log.error(f"Error in deep portfolio review job: {e}", exc_info=True)
        key_ring.handle_api_error(e)

if __name__ == "__main__":
    log.info(f"Initializing System in {config.RUN_MODE} mode...")
    
    if not config.GOOGLE_API_KEYS:
        raise ValueError("No Google API keys found. Please check GOOGLE_API_KEY_1, _2, ... in api.env file.")
    
    shared_kis_api = TradingInterface(mock_trading=config.MOCK_TRADING)
    shared_key_ring = LLMKeyRing(api_keys=config.GOOGLE_API_KEYS, rpd_limit=250)
    shared_db = NewsDatabase()
    
    if config.RUN_MODE == "TRADE":
        active_key = shared_key_ring.get_active_key()
        if not active_key:
            raise Exception("All API keys have reached their quota. Cannot start RAG manager.")
        active_embeddings = GoogleGenerativeAIEmbeddings(model="models/embedding-001", google_api_key=active_key)
        
        shared_rag_manager = RAGManager(active_embeddings)
        shared_trade_monitor = TradeMonitor(shared_kis_api, logger.TradeLogger(), shared_rag_manager)
        
        # 1. get_balance()를 호출하여 전체 계좌 정보를 가져옵니다.
        balance_info = shared_kis_api.get_balance()
        
        # 2. get_balance() 결과가 유효한지 확인합니다.
        if balance_info and "portfolio_kr" in balance_info:
            current_portfolio = balance_info.get("portfolio_kr", []) + balance_info.get("portfolio_us", [])
            
            # 데이터 형식 검증
            is_valid_portfolio = isinstance(current_portfolio, list) and all(isinstance(item, dict) for item in current_portfolio)

            if is_valid_portfolio:
                # 3. 유효한 경우에만 동기화 함수를 실행합니다.
                shared_trade_monitor.sync_with_account(current_portfolio)
                report_message = reporting.format_balance_for_slack(balance_info)
                notification.send_notification(report_message)
            else:
                # 데이터 형식이 올바르지 않으면 에러를 발생시켜 프로그램을 중단시킵니다.
                raise TypeError(f"포트폴리오 데이터 형식이 올바르지 않아 동기화를 중단합니다. 수신된 데이터: {current_portfolio}")
        else:
            # 계좌 정보를 가져올 수 없으면 에러를 발생시켜 프로그램을 중단시킵니다.
            raise ConnectionError("계좌 정보를 가져올 수 없어 동기화를 중단합니다.")
        
        news_job = partial(news_collection_job, shared_kis_api, shared_db)
        kr_job = partial(kr_decision_making_job, shared_kis_api, shared_key_ring, shared_rag_manager, shared_trade_monitor, shared_db)
        us_job = partial(us_decision_making_job, shared_kis_api, shared_key_ring, shared_rag_manager, shared_trade_monitor, shared_db)
        mon_job = partial(monitoring_job, shared_kis_api, shared_key_ring, shared_trade_monitor)
        bal_job = partial(daily_balance_report_job, shared_kis_api)
        risk_job = partial(market_risk_check_job, shared_kis_api, shared_key_ring)
        deep_review_job = partial(deep_portfolio_review_job, shared_kis_api, shared_key_ring, shared_trade_monitor)
        
        schedule.every(10).minutes.do(news_job)
        schedule.every(15).minutes.do(kr_job)
        schedule.every(15).minutes.do(us_job)
        schedule.every(15).minutes.do(mon_job)
        
        schedule.every().day.at("22:30").do(deep_review_job) # 미장 시작
        schedule.every().day.at("05:30").do(deep_review_job) # 미장 마감
        schedule.every().day.at("09:00").do(deep_review_job) # 국장 시작
        schedule.every().day.at("15:30").do(deep_review_job) # 국장 마감
        schedule.every().day.at("08:50").do(bal_job)
        schedule.every().day.at("22:20").do(bal_job)
        schedule.every().day.at("16:00").do(risk_job)
        schedule.every().day.at("06:00").do(risk_job)
                
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
                log.info(f"It's {now.strftime('%H:%M:%S')}. Starting the scheduler loop.")
                break
            time.sleep(0.5) # 0.5초마다 체크하여 정확도 높임

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
        backtest_llm = shared_key_ring.get_llm()
        backtest_embeddings = shared_key_ring.get_embeddings()
        if not (backtest_llm and backtest_embeddings):
             raise Exception("Cannot start backtest: No available API key.")
        watchlist = ['AAPL', 'MSFT', 'GOOGL', 'AMZN', 'NVDA']
        backtester = LLMStrategyBacktester(
            start_date_str="2023-06-01",
            end_date_str="2023-06-30",
            initial_capital=10000000,
            kis_api=shared_kis_api,
            llm=backtest_llm,
            embeddings=backtest_embeddings
        )
        backtester.run(watchlist)
        log.info("--- BACKTEST MODE FINISHED ---")