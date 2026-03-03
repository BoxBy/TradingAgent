import time
from functools import wraps
import config

class RateLimiter:
    """API의 초당 요청 횟수를 제어하는 클래스."""

    def __init__(self, requests_per_second: int = 19):
        self.interval = 1.0 / requests_per_second
        self.last_call_time = 0.0

    def __call__(self, func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            elapsed = time.monotonic() - self.last_call_time
            wait_time = self.interval - elapsed
            if wait_time > 0:
                time.sleep(wait_time)
            result = func(*args, **kwargs)
            self.last_call_time = time.monotonic()
            return result
        return wrapper

# 모의투자: 초당 2건 제한, 실전투자: 초당 19건
_kis_requests_per_second = 2 if config.MOCK_TRADING else 19
kis_api_rate_limiter = RateLimiter(requests_per_second=_kis_requests_per_second)
