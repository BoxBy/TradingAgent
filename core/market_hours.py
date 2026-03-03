from datetime import datetime, time
import pytz

def is_market_open(market_type: str = "KR") -> bool:
    if market_type == "KR":
        return is_kr_market_open()
    elif market_type == "US":
        return is_us_market_open()
    return False

def is_kr_market_open() -> bool:
    tz = pytz.timezone('Asia/Seoul')
    now = datetime.now(tz)
    if now.weekday() >= 5:
        return False
    market_open = time(9, 0)
    market_close = time(15, 30)
    return market_open <= now.time() <= market_close

def is_us_market_open() -> bool:
    tz = pytz.timezone('America/New_York')
    now = datetime.now(tz)
    if now.weekday() >= 5:
        return False
    market_open = time(9, 30)
    market_close = time(16, 0)
    return market_open <= now.time() <= market_close
