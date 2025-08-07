import time
import random
from functools import wraps
from . import logger
from google.api_core import exceptions as google_exceptions
from openai import RateLimitError as OpenaiRateLimitError

log = logger.get_logger(__name__)

def api_retry_decorator(max_retries=3, initial_backoff=2, max_backoff=16):
    """
    API 호출 재시도 데코레이터 (Exponential Backoff with Jitter).
    네트워크 오류(IOError) 등 일반적인 API 오류 발생 시 자동으로 재시도합니다.
    """
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            retries = 0
            backoff = initial_backoff
            while retries < max_retries:
                try:
                    return func(*args, **kwargs)
                # google.api_core.exceptions.ResourceExhausted 와 같은 특정 오류는 제외하고
                # 일반적인 I/O 오류에 대해서만 재시도하도록 수정할 수 있습니다.
                # 여기서는 모든 Exception에 대해 재시도하도록 단순화합니다.
                except Exception as e:
                    # Rate Limit 오류는 상위의 ApiKeyManager가 처리하므로 여기서는 재시도하지 않음
                    if isinstance(e, (google_exceptions.ResourceExhausted, OpenaiRateLimitError)):
                        raise e

                    retries += 1
                    if retries >= max_retries:
                        log.error(f"API call failed after {max_retries} retries for '{func.__name__}'. Error: {e}")
                        raise e
                    
                    sleep_time = backoff + random.uniform(0, 1)
                    log.warning(f"API call failed for '{func.__name__}'. Retrying in {sleep_time:.2f} seconds... (Attempt {retries}/{max_retries})")
                    time.sleep(sleep_time)
                    
                    backoff = min(backoff * 2, max_backoff)
        return wrapper
    return decorator