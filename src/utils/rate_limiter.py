import time
from functools import wraps
from . import logger

log = logger.get_logger(__name__)

class RateLimiter:
    """
    API의 초당 요청 횟수를 제어하는 클래스.
    """
    def __init__(self, requests_per_second: int = 19):
        # KIS API의 초당 요청 제한은 20회이므로, 안전 마진을 두어 19회로 설정합니다.
        self.interval = 1.0 / requests_per_second
        self.last_call_time = 0.0

    def __call__(self, func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            # 마지막 호출 이후 경과 시간을 계산합니다.
            elapsed = time.monotonic() - self.last_call_time
            wait_time = self.interval - elapsed

            # 대기 시간이 필요하면, 그 시간만큼 정확히 대기합니다.
            if wait_time > 0:
                time.sleep(wait_time)

            # 함수를 실행하고 마지막 호출 시간을 기록합니다.
            result = func(*args, **kwargs)
            self.last_call_time = time.monotonic()
            return result
        return wrapper

# 프로그램 전체에서 사용할 KIS API용 전역 RateLimiter 인스턴스를 생성합니다.
kis_api_rate_limiter = RateLimiter()