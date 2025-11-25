from datetime import datetime, timedelta, timezone

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
