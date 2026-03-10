import os
import json
import logging
from datetime import datetime
import pandas as pd

class TradeLogger:
    def __init__(self, log_dir="logs"):
        self.log_dir = log_dir
        os.makedirs(self.log_dir, exist_ok=True)
        self.trades_log_path = os.path.join(self.log_dir, "trades_log.csv")
        self._initialize_files()
        
    def _initialize_files(self):
        if not os.path.exists(self.trades_log_path):
            pd.DataFrame(
                columns=[
                    "Timestamp",
                    "StockCode",
                    "Action",
                    "Quantity",
                    "Price",
                    "Reasoning",
                ]
            ).to_csv(self.trades_log_path, index=False)

    def log_trade(self, stock_code: str, action: str, quantity: int, price: float, reasoning: str):
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        new_log = pd.DataFrame(
            [[timestamp, stock_code, action, quantity, price, reasoning]],
            columns=["Timestamp", "StockCode", "Action", "Quantity", "Price", "Reasoning"]
        )
        new_log.to_csv(self.trades_log_path, mode="a", header=False, index=False)

def get_system_logger(name: str) -> logging.Logger:
    log_dir = "logs"
    os.makedirs(log_dir, exist_ok=True)
    log_file_path = os.path.join(log_dir, "trading_agent.log")
    
    logger = logging.getLogger(name)
    if logger.hasHandlers():
        return logger
        
    logger.setLevel(logging.INFO)
    formatter = logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s")
    
    file_handler = logging.FileHandler(log_file_path, encoding='utf-8')
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)
    
    stream_handler = logging.StreamHandler()
    stream_handler.setFormatter(formatter)
    logger.addHandler(stream_handler)
    
    return logger

class TradeMonitor:
    def __init__(self, state_file="logs/active_trades.json"):
        self.state_file = state_file
        os.makedirs(os.path.dirname(self.state_file), exist_ok=True)
        self.active_trades = self._load_state()

    def _load_state(self) -> dict:
        if os.path.exists(self.state_file):
            try:
                with open(self.state_file, "r") as f:
                    return json.load(f)
            except (json.JSONDecodeError, FileNotFoundError):
                return {}
        return {}

    def _save_state(self):
        with open(self.state_file, "w") as f:
            json.dump(self.active_trades, f, indent=4)

    def register_trade(self, stock_code: str, trade_info: dict):
        self.active_trades[stock_code] = trade_info
        self._save_state()
        
    def remove_trade(self, stock_code: str):
        if stock_code in self.active_trades:
            del self.active_trades[stock_code]
            self._save_state()

    def sync_with_broker_portfolio(self, broker_portfolio: list):
        """
        Reconcile local active_trades.json with the actual broker portfolio.
        broker_portfolio is a list of dicts with 'stock_code', 'quantity', 'average_price', etc.
        """
        new_active_trades = {}
        
        for item in broker_portfolio:
            code = item['stock_code']
            # Preserve existing info if available, otherwise create new entry
            if code in self.active_trades:
                trade_info = self.active_trades[code].copy()
                trade_info['quantity'] = item['quantity']
                trade_info['purchase_price'] = item['average_price']
                new_active_trades[code] = trade_info
            else:
                new_active_trades[code] = {
                    "stock_code": code,
                    "purchase_price": item['average_price'],
                    "quantity": item['quantity'],
                    "status": "active",
                    "market_type": item.get('market_type', 'KR')
                }
        
        # Only overwrite if there are changes to avoid unnecessary writes
        if new_active_trades != self.active_trades:
            self.active_trades = new_active_trades
            self._save_state()
            return True
        return False
