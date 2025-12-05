from datetime import datetime, timedelta, timezone
import json
import os

import holidays

from .. import config
from . import logger

log = logger.get_logger(__name__)


def _is_us_dst(dt: datetime) -> bool:
    if dt.month < 3 or dt.month > 11:
        return False
    if dt.month > 3 and dt.month < 11:
        return True
    past_second_sunday_in_march = dt.replace(day=1)
    sundays = 0
    while sundays < 2:
        if past_second_sunday_in_march.weekday() == 6:
            sundays += 1
        if sundays < 2:
            past_second_sunday_in_march += timedelta(days=1)
    past_first_sunday_in_november = dt.replace(day=1)
    while past_first_sunday_in_november.weekday() != 6:
        past_first_sunday_in_november += timedelta(days=1)
    if dt.month == 3:
        return dt >= past_second_sunday_in_march
    if dt.month == 11:
        return dt < past_first_sunday_in_november
    return False


def _get_us_eastern_time() -> datetime:
    utc_now = datetime.now(timezone.utc)
    offset_hours = -4 if _is_us_dst(utc_now) else -5
    return utc_now + timedelta(hours=offset_hours)


def is_us_market_open() -> bool:
    if config.RUN_MODE == "BACKTEST":
        log.info("BACKTEST mode is active. Simulating US market as open.")
        return True

    et_now = _get_us_eastern_time()
    us_holidays = holidays.US(state="NY")

    if et_now.weekday() >= 5 or et_now.date() in us_holidays:
        log.info(
            f"US Market is closed. Reason: Weekend or Holiday ({et_now.strftime('%Y-%m-%d')})"
        )
        return False

    market_open = et_now.replace(hour=9, minute=30, second=0, microsecond=0).time()
    market_close = et_now.replace(hour=16, minute=0, second=0, microsecond=0).time()

    if market_open <= et_now.time() <= market_close:
        log.info("US Market is open.")
        return True
    else:
        log.info(
            f"US Market is closed. Current time (ET): {et_now.strftime('%H:%M:%S')}"
        )
        return False


def is_kr_market_open() -> bool:
    if config.RUN_MODE == "BACKTEST":
        log.info("BACKTEST mode is active. Simulating KR market as open.")
        return True

    kst_now = datetime.now(timezone.utc) + timedelta(hours=9)
    kr_holidays = holidays.KR()

    if kst_now.weekday() >= 5 or kst_now.date() in kr_holidays:
        log.info(
            f"KR Market is closed. Reason: Weekend or Holiday ({kst_now.strftime('%Y-%m-%d')})"
        )
        return False

    market_open = kst_now.replace(hour=9, minute=0, second=0, microsecond=0).time()
    market_close = kst_now.replace(hour=15, minute=30, second=0, microsecond=0).time()

    if market_open <= kst_now.time() <= market_close:
        log.info("KR Market is open.")
        return True
    else:
        log.info(
            f"KR Market is closed. Current time (KST): {kst_now.strftime('%H:%M:%S')}"
        )
        return False

MARKET_REPORTS_FILE = os.path.join(config.LOG_DIR, "market_conditions_history.json")

def load_previous_market_reports(limit: int = 3) -> str:
    """
    최근 N개의 시장 위험 보고서를 로드하여 문자열로 반환합니다.
    """
    if not os.path.exists(MARKET_REPORTS_FILE):
        return "No previous reports available."
    
    try:
        with open(MARKET_REPORTS_FILE, "r") as f:
            reports = json.load(f)
        
        # 최신순으로 정렬되어 있다고 가정 (append로 추가하므로 뒤쪽이 최신)
        recent_reports = reports[-limit:]
        
        formatted_reports = []
        for r in recent_reports:
            timestamp = r.get("timestamp", "Unknown Time")
            vix = r.get("vix", "N/A")
            reasoning = r.get("reasoning", "N/A")
            formatted_reports.append(f"[{timestamp}] VIX: {vix} | Reason: {reasoning}")
            
        return "\n".join(formatted_reports)
    except Exception as e:
        log.error(f"Failed to load previous market reports: {e}")
        return "Error loading reports."

def save_market_report(report_data: dict):
    """
    새로운 시장 위험 보고서를 히스토리 파일에 저장합니다.
    """
    try:
        reports = []
        if os.path.exists(MARKET_REPORTS_FILE):
            with open(MARKET_REPORTS_FILE, "r") as f:
                try:
                    reports = json.load(f)
                except json.JSONDecodeError:
                    reports = []
        
        # 타임스탬프 추가
        report_data["timestamp"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        reports.append(report_data)
        
        # 너무 많이 쌓이지 않도록 최근 50개만 유지
        if len(reports) > 50:
            reports = reports[-50:]
            
        with open(MARKET_REPORTS_FILE, "w") as f:
            json.dump(reports, f, indent=2)
            
    except Exception as e:
        log.error(f"Failed to save market report: {e}")
