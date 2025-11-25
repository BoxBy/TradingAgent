import logging
import os
import sys
import json
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, List, Dict

import pandas as pd

# 내부 모듈 import 경로 수정
from .. import config

# 순환 참조 방지를 위한 타입 힌트
if TYPE_CHECKING:
    from ..data_providers import TradingInterface


def _is_us_dst(dt_utc: datetime) -> bool:
    if dt_utc.month < 3 or dt_utc.month > 11:
        return False
    if dt_utc.month > 3 and dt_utc.month < 11:
        return True

    past_second_sunday_in_march = dt_utc.replace(day=1)
    sundays = 0
    while sundays < 2:
        if past_second_sunday_in_march.weekday() == 6:
            sundays += 1
        if sundays < 2:
            past_second_sunday_in_march += timedelta(days=1)

    past_first_sunday_in_november = dt_utc.replace(day=1)
    while past_first_sunday_in_november.weekday() != 6:
        past_first_sunday_in_november += timedelta(days=1)

    if dt_utc.month == 3:
        return dt_utc >= past_second_sunday_in_march
    if dt_utc.month == 11:
        return dt_utc < past_first_sunday_in_november
    return False


def _get_us_eastern_time() -> datetime:
    utc_now = datetime.now(timezone.utc)
    offset_hours = -4 if _is_us_dst(utc_now) else -5
    return utc_now + timedelta(hours=offset_hours)


def _get_us_market_close_period_key(et_now: datetime) -> str:
    market_close = et_now.replace(hour=16, minute=0, second=0, microsecond=0)
    if et_now >= market_close:
        key_date = et_now.date()
    else:
        key_date = (et_now - timedelta(days=1)).date()
    return key_date.isoformat()


class USMarketCloseFileHandler(logging.Handler):
    """미국 동부시간 기준 장 마감(16:00 ET)을 경계로 일별 로그를 분리하는 핸들러."""

    def __init__(self, filename: str, backupCount: int = 5, encoding: str = "utf-8"):
        super().__init__()
        self.baseFilename = os.path.abspath(filename)
        self.backupCount = backupCount
        self.encoding = encoding
        self.terminator = "\n"

        et_now = _get_us_eastern_time()
        self.current_period_key = _get_us_market_close_period_key(et_now)

        self.stream = self._open()
        self.createLock()

    def _open(self):
        return open(self.baseFilename, mode="a", encoding=self.encoding)

    def _should_rollover(self) -> bool:
        et_now = _get_us_eastern_time()
        new_key = _get_us_market_close_period_key(et_now)
        return new_key != self.current_period_key

    def _get_files_to_delete(self):
        if self.backupCount <= 0:
            return []

        dir_name, base_name = os.path.split(self.baseFilename)
        try:
            file_names = os.listdir(dir_name)
        except FileNotFoundError:
            return []

        prefix = base_name + "."
        result = [
            os.path.join(dir_name, f)
            for f in file_names
            if f.startswith(prefix)
        ]
        result.sort()

        if len(result) <= self.backupCount:
            return []

        return result[0 : len(result) - self.backupCount]

    def doRollover(self) -> None:
        if self.stream:
            try:
                self.stream.close()
            except Exception:
                pass
            self.stream = None

        if os.path.exists(self.baseFilename):
            dfn = f"{self.baseFilename}.{self.current_period_key}"
            if not os.path.exists(dfn):
                try:
                    os.rename(self.baseFilename, dfn)
                except OSError:
                    pass

        for s in self._get_files_to_delete():
            try:
                os.remove(s)
            except OSError:
                pass

        et_now = _get_us_eastern_time()
        self.current_period_key = _get_us_market_close_period_key(et_now)
        self.stream = self._open()

    def emit(self, record: logging.LogRecord) -> None:
        try:
            if self._should_rollover():
                self.doRollover()

            msg = self.format(record)
            stream = self.stream
            stream.write(msg + self.terminator)
            stream.flush()
        except Exception:
            self.handleError(record)

    def close(self) -> None:
        try:
            if self.stream:
                try:
                    self.stream.close()
                except Exception:
                    pass
                self.stream = None
        finally:
            super().close()


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
    file_handler = USMarketCloseFileHandler(
        log_file_path, backupCount=5, encoding="utf-8"
    )
    file_handler.setFormatter(formatter)
    log.addHandler(file_handler)

    class SlackExceptionHandler(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            try:
                # Only handle error or higher
                if record.levelno < logging.ERROR:
                    return
                # Avoid recursion from notification module itself
                if record.name.startswith("src.utils.notification"):
                    return

                message_text = self.format(record)
                lower = message_text.lower()
                # Skip known quota / rate limit noise
                if "api rate limit hit" in lower or "quota" in lower or "resourceexhausted" in lower:
                    return

                # Skip noisy KIS price fetch failures from daily error counting
                if record.name.startswith("src.data_providers.kis_wrapper") and "failed to fetch valid price" in lower:
                    return

                # Daily error count state 파일에 누적 기록
                state_file = os.path.join(config.LOG_DIR, "daily_error_stats.json")
                today_str = datetime.now().strftime("%Y-%m-%d")
                try:
                    if os.path.exists(state_file):
                        with open(state_file, "r") as f:
                            state = json.load(f)
                    else:
                        state = {}
                except Exception:
                    state = {}

                if state.get("date") != today_str:
                    state = {"date": today_str, "error_count": 0}

                try:
                    current_count = int(state.get("error_count", 0))
                except (TypeError, ValueError):
                    current_count = 0

                state["error_count"] = current_count + 1

                try:
                    with open(state_file, "w") as f:
                        json.dump(state, f, indent=4)
                except Exception:
                    # 로깅용 파일 기록 실패는 무시
                    pass
            except Exception:
                # Do not raise from logging handler
                pass

    slack_handler = SlackExceptionHandler()
    slack_handler.setLevel(logging.ERROR)
    slack_handler.setFormatter(formatter)
    log.addHandler(slack_handler)

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
