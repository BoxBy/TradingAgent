import json
import os
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, List, Optional, Type

from google.api_core import exceptions as google_exceptions
from langchain_core.messages import BaseMessage
from langchain_core.runnables import Runnable
from langchain_core.runnables.config import RunnableConfig
from langchain_google_genai import ChatGoogleGenerativeAI, GoogleGenerativeAIEmbeddings
from langchain_google_genai._common import GoogleGenerativeAIError
from langchain_core.tools import BaseTool

from .. import config
from . import logger

log = logger.get_logger(__name__)


class LLMProvider:
    """
    여러 Gemini API 키를 순환하며 ChatGoogleGenerativeAI와
    GoogleGenerativeAIEmbeddings를 사용하는 완전한 기능을 갖춘 LLM 제공 클래스.
    'ResourceExhausted' (429) 오류 발생 시 자동으로 다음 키로 전환하고 재시도합니다.
    """

    STATE_FILE = os.path.join(config.LOG_DIR, "api_key_usage.json")

    def __init__(self, api_keys: List[str]):
        if not api_keys:
            raise ValueError("At least one API key is required.")
        self.api_keys = api_keys
        self.num_keys = len(api_keys)
        log.info(f"{self.num_keys} keys are found.")
        self.current_key_index = 0
        self._load_state()

    def _get_pst_date_str(self) -> str:
        pst_timezone = timezone(timedelta(hours=-8))
        return datetime.now(pst_timezone).strftime("%Y-%m-%d")

    def _load_state(self):
        today_str = self._get_pst_date_str()
        try:
            if os.path.exists(self.STATE_FILE):
                with open(self.STATE_FILE, "r") as f:
                    state = json.load(f)
            else:
                state = {}
        except (json.JSONDecodeError, FileNotFoundError):
            state = {}

        if state.get("date") != today_str:
            log.info(
                f"New PST date ({today_str}) detected. Resetting API usage counts."
            )
            state = {"date": today_str, "usage": {}}

        self.date = state.get("date", today_str)
        self.usage = state.get("usage", {})
        self.current_key_index = state.get("current_key_index", 0)

    def _save_state(self):
        state = {
            "date": self.date,
            "usage": self.usage,
            "current_key_index": self.current_key_index,
        }
        with open(self.STATE_FILE, "w") as f:
            json.dump(state, f, indent=4)

    def _get_key_alias(self, api_key: str) -> str:
        return f"key_{api_key[:8]}"

    def _increment_usage(self, call_type: str, cost: int = 1):
        active_key = self.api_keys[self.current_key_index]
        key_alias = self._get_key_alias(active_key)
        if key_alias not in self.usage:
            self.usage[key_alias] = {"llm": 0, "embedding": 0}
        self.usage[key_alias][call_type] = self.usage[key_alias].get(call_type, 0) + cost
        log.info(f"API usage for key '{key_alias}' -> {self.usage[key_alias]}")
        self._save_state()

    def handle_api_error(self, e: Exception, attempt: int):
        if isinstance(e, google_exceptions.ResourceExhausted) or isinstance(e, GoogleGenerativeAIError):
            key_alias = self._get_key_alias(self.api_keys[self.current_key_index])
            log.warning(
                f"API Rate Limit hit for key '{key_alias}' (Attempt {attempt + 1}/{self.num_keys}). Switching to the next key."
            )
            self.current_key_index = (self.current_key_index + 1) % self.num_keys
            self._save_state()
        else:
            log.error(f"An unexpected error occurred: {e}", exc_info=True)

    def _execute_with_retry(self, call_type: str, func: Callable[..., Any], *args, **kwargs) -> Any:
        """
        API 호출을 실행하고, 사용량 초과 시 자동으로 재시도하는 로직.
        """
        self._load_state()
        for i in range(self.num_keys):
            try:
                result = func(*args, **kwargs)
                self._increment_usage(call_type)
                return result
            except Exception as e:
                self.handle_api_error(e, attempt=i)

        raise google_exceptions.ResourceExhausted(
            "All API keys have been tried and failed. Please check their status."
        )

    def get_llm(self) -> ChatGoogleGenerativeAI:
        """
        현재 활성화된 API 키로 ChatGoogleGenerativeAI 인스턴스를 반환합니다.
        """
        active_key = self.api_keys[self.current_key_index]
        return ChatGoogleGenerativeAI(
            model=config.LLM_MODEL_NAME, temperature=0.2, google_api_key=active_key, convert_system_message_to_human=True
        )

    def get_embeddings(self) -> GoogleGenerativeAIEmbeddings:
        """
        현재 활성화된 API 키로 GoogleGenerativeAIEmbeddings 인스턴스를 반환합니다.
        """
        active_key = self.api_keys[self.current_key_index]
        return GoogleGenerativeAIEmbeddings(
            model="gemini-embedding-001", google_api_key=active_key
        )

    # ✅ 'bound_tools' 인자를 받도록 수정
    def invoke(self, input: Any, config: Optional[RunnableConfig] = None, bound_tools: Optional[List[Callable]] = None) -> Any:
        """
        LLM에 프롬프트를 보내고 응답을 받습니다. 키 순환 및 재시도를 자동으로 처리합니다.
        """

        def api_call():
            llm = self.get_llm()
            # ✨ 도구가 있으면 LLM에 바인딩
            if bound_tools:
                llm = llm.bind_tools(bound_tools)
            return llm.invoke(input, config=config)

        return self._execute_with_retry("llm", api_call)

    # ✅ 'bound_tools' 인자를 받도록 수정
    def stream(self, input: Any, config: Optional[RunnableConfig] = None, bound_tools: Optional[List[Callable]] = None) -> Any:
        """
        LLM으로부터 스트리밍 응답을 받습니다. 키 순환 및 재시도를 자동으로 처리합니다.
        """

        def api_call():
            llm = self.get_llm()
            # ✨ 도구가 있으면 LLM에 바인딩
            if bound_tools:
                llm = llm.bind_tools(bound_tools)
            return llm.stream(input, config=config)

        return self._execute_with_retry("llm", api_call)

    def embed_query(self, text: str) -> List[float]:
        """
        주어진 텍스트에 대한 임베딩을 생성합니다.
        """

        def api_call():
            embeddings = self.get_embeddings()
            return embeddings.embed_query(text)

        return self._execute_with_retry("embedding", api_call)

    def embed_documents(self, texts: List[str]) -> List[List[float]]:
        """
        주어진 문서 목록에 대한 임베딩을 생성합니다.
        """

        def api_call():
            embeddings = self.get_embeddings()
            return embeddings.embed_documents(texts)

        return self._execute_with_retry("embedding", api_call)


# LLMProvider 인스턴스 생성
llm_provider = LLMProvider(config.GOOGLE_API_KEYS)

# LangChain Runnable 인터페이스를 준수하도록 LLMProvider를 래핑하는 클래스
class RunnableLLMProvider(Runnable):
    provider: LLMProvider
    bound_tools: Optional[List[Callable | Type[BaseTool] | dict]] = None

    # ✅ __init__ 메소드를 LangChain Runnable 표준에 맞게 수정
    def __init__(self, provider: LLMProvider, **kwargs: Any):
        super().__init__(**kwargs)
        self.provider = provider

    # ✅ bind_tools 메소드 구현
    def bind_tools(
        self, tools: List[Callable | Type[BaseTool] | dict]
    ) -> "RunnableLLMProvider":
        """도구를 바인딩하고 새 Runnable 인스턴스를 반환합니다."""
        # self.provider는 그대로 전달하고, bound_tools만 새로 설정
        new_runnable = RunnableLLMProvider(provider=self.provider)
        new_runnable.bound_tools = tools
        return new_runnable

    # ✅ invoke가 bound_tools를 provider로 전달하도록 수정
    def invoke(self, input: Any, config: Optional[RunnableConfig] = None) -> Any:
        return self.provider.invoke(input, config, bound_tools=self.bound_tools)

    # ✅ stream이 bound_tools를 provider로 전달하도록 수정
    def stream(self, input: Any, config: Optional[RunnableConfig] = None) -> Any:
        return self.provider.stream(input, config, bound_tools=self.bound_tools)
    
    def embed_query(self, text: str) -> List[float]:
        """내부 provider의 embed_query를 호출합니다."""
        return self.provider.embed_query(text)

    def embed_documents(self, texts: List[str]) -> List[List[float]]:
        """내부 provider의 embed_documents를 호출합니다."""
        return self.provider.embed_documents(texts)

# 전역적으로 사용될 RunnableLLMProvider 인스턴스
runnable_llm_provider = RunnableLLMProvider(provider=llm_provider)
