import os
import json
import logging
from logging.handlers import TimedRotatingFileHandler
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
    
    file_handler = TimedRotatingFileHandler(
        log_file_path, when='midnight', interval=1, backupCount=30, encoding='utf-8'
    )
    file_handler.suffix = '%Y-%m-%d'
    file_handler.setFormatter(formatter)
    # Flush immediately on each log write to prevent data loss on crash
    _orig_emit = file_handler.emit
    def _flush_emit(record):
        _orig_emit(record)
        file_handler.flush()
    file_handler.emit = _flush_emit
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
        
        Bug fix (2026-05-16): Skip sync when broker returns empty portfolio to prevent
        wiping all positions during KIS API intermittent empty-response glitches.
        """
        logger = logging.getLogger('main')
        
        # GUARD: If broker returns empty but we have existing positions, skip sync
        # This prevents API glitches from wiping our position tracking
        if not broker_portfolio and self.active_trades:
            logger.warning(
                f"[TradingClaw] Broker returned empty portfolio but {len(self.active_trades)} "
                f"positions tracked. Skipping sync to prevent data loss (likely KIS API glitch)."
            )
            return False
        
        # Skip positions flagged as phantom (KIS has them in query but can't trade them)
        _logs_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'logs')
        _phantom_tickers = set()
        if os.path.isdir(_logs_dir):
            for _f in os.listdir(_logs_dir):
                if _f.startswith("phantom_") and _f.endswith(".flag"):
                    _phantom_tickers.add(_f.replace("phantom_", "").replace(".flag", "").upper())
        
        if _phantom_tickers:
            _before = len(broker_portfolio)
            broker_portfolio = [p for p in broker_portfolio if p.get('stock_code', '').upper() not in _phantom_tickers]
            if len(broker_portfolio) < _before:
                logger.info(f"[TradingClaw] Sync: filtered {_before - len(broker_portfolio)} phantom position(s) from broker data: {_phantom_tickers}")
        
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
        
        # GUARD: If sync would remove ALL positions, skip (safety net)
        if not new_active_trades and self.active_trades:
            logger.warning(
                f"[TradingClaw] Sync would wipe {len(self.active_trades)} positions. "
                f"Skipping (likely KIS API glitch)."
            )
            return False
        
        # Only overwrite if there are changes to avoid unnecessary writes
        if new_active_trades != self.active_trades:
            self.active_trades = new_active_trades
            self._save_state()
            return True
        return False
