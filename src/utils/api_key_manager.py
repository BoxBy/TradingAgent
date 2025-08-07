import os
import json
from datetime import date, timedelta, timezone, datetime
from typing import List, Optional
from google.api_core import exceptions as google_exceptions

from .. import config
from . import logger

log = logger.get_logger(__name__)

class LLMKeyRing:
    """
    여러 Gemini API 키의 일일 사용량(RPD)을 추적하고,
    'ResourceExhausted' (429) 오류 발생 시 다음 키로 순환하는 클래스.
    """
    STATE_FILE = os.path.join(config.LOG_DIR, "api_key_usage.json")

    def __init__(self, api_keys: List[str], rpd_limit: int):
        if not api_keys:
            raise ValueError("At least one API key is required.")
        self.api_keys = api_keys
        self.num_keys = len(api_keys)
        log.info(f"{self.num_keys} keys are Found")
        self.rpd_limit = rpd_limit
        self.current_key_index = 0
        self._load_state()

    def _get_pst_date_str(self) -> str:
        # 태평양 표준시(PST, UTC-8) 기준으로 날짜를 계산합니다.
        pst_timezone = timezone(timedelta(hours=-8))
        pst_now = datetime.now(pst_timezone)
        return pst_now.strftime('%Y-%m-%d')

    def _load_state(self):
        # 파일에서 현재 사용량 상태를 불러옵니다.
        today_str = self._get_pst_date_str()
        try:
            if os.path.exists(self.STATE_FILE):
                with open(self.STATE_FILE, 'r') as f: state = json.load(f)
            else: state = {}
        except (json.JSONDecodeError, FileNotFoundError): state = {}
        
        # 날짜가 바뀌었으면 카운트를 리셋합니다.
        if state.get("date") != today_str:
            log.info(f"New PST date ({today_str}) detected. Resetting all API usage counts.")
            state = {"date": today_str, "usage": {}}
        
        self.date = state.get("date", today_str)
        self.usage = state.get("usage", {})
        self._find_active_key_index()

    def _save_state(self):
        # 현재 사용량 상태를 파일에 저장합니다.
        state = { "date": self.date, "usage": self.usage }
        with open(self.STATE_FILE, 'w') as f:
            json.dump(state, f, indent=4)

    def _get_key_alias(self, api_key: str) -> str:
        # 보안을 위해 실제 키 대신 앞 8자리만 별칭으로 사용합니다.
        return f"key_{api_key[:8]}"

    def _find_active_key_index(self):
        # 사용 가능한 첫 번째 키의 인덱스를 찾습니다.
        for i in range(self.num_keys):
            key = self.api_keys[i]
            key_alias = self._get_key_alias(key)
            if self.usage.get(key_alias, 0) < self.rpd_limit:
                if self.current_key_index != i:
                    log.info(f"Active API key switched from index {self.current_key_index} to {i}.")
                self.current_key_index = i
                log.info(f"Active API key set to index {i} ('{self._get_key_alias(self.api_keys[i])}').")
                return
        log.error("All API keys have reached their RPD limit for today.")

    def get_active_key(self) -> Optional[str]:
        """
        사용 가능한 API 키 '문자열'을 반환합니다. 없으면 None을 반환합니다.
        """
        # API 호출 전, 날짜가 바뀌었는지 항상 확인하여 카운트를 리셋합니다.
        today_str = self._get_pst_date_str()
        if self.date != today_str:
            self._load_state()

        # 1. 현재 키가 소진되었는지 먼저 확인합니다.
        current_key_alias = self._get_key_alias(self.api_keys[self.current_key_index])
        if self.usage.get(current_key_alias, 0) >= self.rpd_limit:
            log.warning(f"API key '{current_key_alias}' has reached its quota. Finding the next available key...")
            # 2. 소진되었다면, 다음 사용 가능한 키를 찾습니다.
            self._find_active_key_index()
        
        # 3. 모든 키 전환 시도 후, 최종적으로 선택된 키를 다시 확인합니다.
        final_key_alias = self._get_key_alias(self.api_keys[self.current_key_index])
        if self.usage.get(final_key_alias, 0) >= self.rpd_limit:
            # 그래도 키가 소진 상태라면, 모든 키가 소진된 것입니다.
            log.error("Failed to get active key: All API keys have reached their daily quota.")
            return None
            
        # 4. 최종 확인을 통과한 유효한 키를 반환합니다.
        return self.api_keys[self.current_key_index]

    def increment_usage(self, cost=1):
        # 현재 활성화된 키의 사용량을 증가시킵니다.
        active_key = self.api_keys[self.current_key_index]
        key_alias = self._get_key_alias(active_key)
        
        current_usage = self.usage.get(key_alias, 0) + cost
        self.usage[key_alias] = current_usage
        
        log.info(f"API usage for key '{key_alias}' -> {current_usage}/{self.rpd_limit}")
        self._save_state()

    def handle_api_error(self, e: Exception):
        # API 사용량 초과 오류 발생 시, 해당 키를 소진 처리하고 다음 키로 전환합니다.
        if isinstance(e, google_exceptions.ResourceExhausted):
            key_alias = self._get_key_alias(self.api_keys[self.current_key_index])
            log.warning(f"API Rate Limit hit for key '{key_alias}'. Marking as exhausted and switching.")
            self.usage[key_alias] = self.rpd_limit
            self._save_state()
            self._find_active_key_index()
        else:
            # 다른 종류의 에러는 그대로 다시 발생시켜 상위에서 처리하도록 함
            log.warning(f"{e}")
            raise e