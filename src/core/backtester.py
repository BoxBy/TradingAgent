import pandas as pd
from datetime import datetime, timedelta
import numpy as np
from typing import List, Dict

from ..data_providers import DataIngestion
from ..utils import logger

log = logger.get_logger(__name__)

class LLMStrategyBacktester:
    def __init__(self, start_date_str: str, end_date_str: str, initial_capital: float, kis_api, llm, embeddings):
        self.start_date = datetime.strptime(start_date_str, "%Y-%m-%d")
        self.end_date = datetime.strptime(end_date_str, "%Y-%m-%d")
        self.initial_capital = initial_capital
        self.cash = initial_capital
        self.kis_api = kis_api
        self.llm = llm
        self.embeddings = embeddings
        self.data_ingestor = DataIngestion(self.kis_api)
        self.portfolio = {}
        self.trade_log = []
        self.portfolio_history = []

    def run(self, watchlist: List[str]):
        log.info(f"--- Starting LLM Strategy Backtest from {self.start_date.date()} to {self.end_date.date()} ---")

        # ✨ orchestrator를 run 메소드 내부에서 import하여 순환 참조를 해결합니다.
        from ..orchestrators import TradingOrchestrator
        from ..services import RAGManager

        for current_date in pd.date_range(start=self.start_date, end=self.end_date, freq='B'):
            log.info(f"--- Backtesting for date: {current_date.strftime('%Y-%m-%d')} ---")
            
            all_stocks_data = self._gather_data_for_date(watchlist, current_date)

            if not all_stocks_data:
                log.warning(f"No data available for {current_date.date()}, skipping.")
                self._record_portfolio_value(all_stocks_data, current_date)
                continue

            temp_rag_manager = RAGManager(self.embeddings, path="./rag_db_backtest")
            orchestrator = TradingOrchestrator(temp_rag_manager, self.llm, [])
            decisions = orchestrator.get_batch_trade_decisions(all_stocks_data, self._get_current_balance())

            self._execute_virtual_trades(decisions, all_stocks_data, current_date)
            self._record_portfolio_value(all_stocks_data, current_date)

        return self._calculate_performance_metrics()

    def _gather_data_for_date(self, watchlist: List[str], current_date: datetime) -> Dict:
        all_data = {}
        end_str = current_date.strftime('%Y-%m-%d')
        start_str = (current_date - timedelta(days=365)).strftime('%Y-%m-%d')

        for stock in watchlist:
            ohlcv = self.data_ingestor.get_technical_data(stock, 'US')
            if ohlcv is not None and not ohlcv.empty:
                ohlcv = ohlcv[ohlcv.index < current_date]
            
            all_data[stock] = {
                "ohlcv": ohlcv,
                "news": self.data_ingestor.get_company_news(stock, start_str, end_str),
                "fundamentals": self.data_ingestor.get_fundamental_data(stock)
            }
        return all_data

    def _get_current_balance(self) -> Dict:
        return {"total_assets": 0, "cash_balance": self.cash, "portfolio": list(self.portfolio.values())}

    def _execute_virtual_trades(self, decisions: List[Dict], all_stocks_data: Dict, current_date: datetime):
        for decision in decisions:
            stock = decision.get("stock_code")
            action = decision.get("decision")
            
            ohlcv = all_stocks_data.get(stock, {}).get("ohlcv")
            if ohlcv is None or ohlcv.empty:
                continue
            price = ohlcv.iloc[-1]['Close']
            
            if action == "BUY":
                quantity = decision.get("quantity", 1)
                cost = price * quantity
                if self.cash >= cost:
                    self.cash -= cost
                    current_qty = self.portfolio.get(stock, {}).get("quantity", 0)
                    self.portfolio[stock] = {"quantity": current_qty + quantity, "average_price": price}
                    self.trade_log.append(f"{current_date.date()}: BUY {quantity} {stock} @ {price:.2f}")

            elif action == "SELL" and stock in self.portfolio:
                quantity = self.portfolio[stock]["quantity"]
                self.cash += price * quantity
                del self.portfolio[stock]
                self.trade_log.append(f"{current_date.date()}: SELL {quantity} {stock} @ {price:.2f}")

    def _record_portfolio_value(self, all_stocks_data: Dict, current_date: datetime):
        stock_value = 0
        for stock, data in self.portfolio.items():
            ohlcv = all_stocks_data.get(stock, {}).get("ohlcv")
            if ohlcv is not None and not ohlcv.empty:
                price = ohlcv.iloc[-1]['Close']
                stock_value += price * data["quantity"]
        
        self.portfolio_history.append({"Date": current_date, "Value": self.cash + stock_value})
    
    def _calculate_performance_metrics(self) -> Dict:
        if not self.portfolio_history:
            log.warning("No portfolio history recorded. Cannot calculate performance.")
            return {}

        history_df = pd.DataFrame(self.portfolio_history).set_index("Date")
        
        total_return = (history_df['Value'].iloc[-1] / self.initial_capital - 1) * 100
        days = (history_df.index[-1] - history_df.index[0]).days
        cagr = ((1 + total_return / 100) ** (365.0 / days) - 1) * 100 if days > 0 else 0
        
        history_df['DailyReturn'] = history_df['Value'].pct_change().fillna(0)
        annualized_volatility = history_df['DailyReturn'].std() * np.sqrt(252) * 100
        
        sharpe_ratio = (cagr / annualized_volatility) if annualized_volatility != 0 else 0

        rolling_max = history_df['Value'].cummax()
        daily_drawdown = history_df['Value'] / rolling_max - 1.0
        max_drawdown = daily_drawdown.min() * 100

        log.info("--- Backtest Results ---")
        log.info(f"Final Portfolio Value: {history_df['Value'].iloc[-1]:,.0f} KRW")
        log.info(f"Total Return: {total_return:.2f}%")
        log.info(f"CAGR: {cagr:.2f}%")
        log.info(f"Annualized Volatility: {annualized_volatility:.2f}%")
        log.info(f"Sharpe Ratio: {sharpe_ratio:.2f}")
        log.info(f"Max Drawdown: {max_drawdown:.2f}%")
        log.info(f"Number of Trades: {len(self.trade_log)}")

        return {
            "total_return_percent": total_return,
            "cagr_percent": cagr,
            "sharpe_ratio": sharpe_ratio,
            "max_drawdown_percent": max_drawdown
        }