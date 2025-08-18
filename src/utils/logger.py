import logging
import os
import sys
from datetime import datetime
from logging.handlers import RotatingFileHandler
from typing import TYPE_CHECKING

import pandas as pd

# 내부 모듈 import 경로 수정
from .. import config

# 순환 참조 방지를 위한 타입 힌트
if TYPE_CHECKING:
    from ..data_providers import TradingInterface


def get_logger(name: str) -> logging.Logger:
    """설정된 로거 인스턴스를 반환합니다."""
    log = logging.getLogger(name)
    if log.hasHandlers():
        return log

    log.setLevel(logging.INFO)
    log.propagate = False

    formatter = logging.Formatter(
        "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
    )

    stream_handler = logging.StreamHandler(sys.stdout)
    stream_handler.setFormatter(formatter)
    log.addHandler(stream_handler)

    log_file_path = os.path.join(config.LOG_DIR, "trading_agent.log")
    file_handler = RotatingFileHandler(
        log_file_path, maxBytes=10 * 1024 * 1024, backupCount=5, encoding="utf-8"
    )
    file_handler.setFormatter(formatter)
    log.addHandler(file_handler)

    return log


class TradeLogger:
    """거래 내역과 일일 성과를 기록하는 로거 클래스."""

    def __init__(self):
        self.log_dir = config.LOG_DIR
        self.trades_log_path = os.path.join(self.log_dir, "trades_log.csv")
        self.pnl_log_path = os.path.join(self.log_dir, "daily_performance.xlsx")
        self._initialize_files()

    def _initialize_files(self):
        """로그 파일이 없으면 헤더와 함께 생성합니다."""
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
        if not os.path.exists(self.pnl_log_path):
            with pd.ExcelWriter(self.pnl_log_path, engine="openpyxl") as writer:
                pd.DataFrame(
                    columns=["Date", "TotalAssets", "DailyPNL", "DailyReturn_pct"]
                ).to_excel(writer, index=False, sheet_name="Performance")

    def log_trade(
        self, stock_code: str, action: str, quantity: int, price: float, reasoning: str
    ):
        """개별 거래 내역을 CSV 파일에 기록합니다."""
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        new_log = pd.DataFrame(
            [[timestamp, stock_code, action, quantity, price, reasoning]],
            columns=[
                "Timestamp",
                "StockCode",
                "Action",
                "Quantity",
                "Price",
                "Reasoning",
            ],
        )
        new_log.to_csv(self.trades_log_path, mode="a", header=False, index=False)
        get_logger(__name__).info(
            f"Trade logged: {action} {quantity} of {stock_code} at {price}"
        )

    def log_daily_pnl(self, kis_wrapper: "TradingInterface"):
        """일일 계좌 손익을 Excel 파일에 기록합니다."""
        log = get_logger(__name__)
        log.info("Logging daily P&L...")
        try:
            balance_info = kis_wrapper.get_balance()
            if not balance_info or "total_assets" not in balance_info:
                log.warning("Could not log P&L, failed to fetch balance.")
                return

            total_assets = balance_info["total_assets"]
            today_str = datetime.now().strftime("%Y-%m-%d")

            try:
                df = pd.read_excel(self.pnl_log_path, sheet_name="Performance")
            except FileNotFoundError:
                df = pd.DataFrame(
                    columns=["Date", "TotalAssets", "DailyPNL", "DailyReturn_pct"]
                )

            if not df.empty and today_str in df["Date"].astype(str).values:
                log.info("Today's P&L has already been logged.")
                return

            if not df.empty:
                last_day_assets = df["TotalAssets"].iloc[-1]
                daily_pnl = total_assets - last_day_assets
                daily_return = (
                    (daily_pnl / last_day_assets) * 100 if last_day_assets != 0 else 0
                )
            else:  # 첫 거래일
                daily_pnl = 0
                daily_return = 0

            new_pnl_log = pd.DataFrame(
                [[today_str, total_assets, daily_pnl, daily_return]],
                columns=["Date", "TotalAssets", "DailyPNL", "DailyReturn_pct"],
            )

            df = pd.concat([df, new_pnl_log], ignore_index=True)

            with pd.ExcelWriter(
                self.pnl_log_path,
                engine="openpyxl",
                mode="a",
                if_sheet_exists="replace",
            ) as writer:
                df.to_excel(writer, index=False, sheet_name="Performance")

            log.info("Daily P&L logged successfully.")
        except Exception as e:
            log.error(f"Failed to log daily P&L: {e}", exc_info=True)
