import os
import json
import asyncio
import time
import logging
from openai import AsyncOpenAI
from typing import List, Dict, Any, Optional

from config import (get_api_key, LLM_ENDPOINT, LLM_API_KEY, LLM_MODEL,
                   LLM_MAX_RETRIES, LLM_RETRY_DELAY_BASE, LLM_RETRY_DELAY_MAX,
                   LLM_BACKOFF_MULTIPLIER)
from core.tools import CORE_TOOLS_SCHEMA, dispatch_core_tool
from core.market_hours import is_market_open

# LLM API 로깅 설정
llm_logger = logging.getLogger('llm_api')
llm_file_handler = logging.FileHandler('logs/llm_api.log')
llm_file_handler.setFormatter(logging.Formatter('%(asctime)s - %(levelname)s - %(message)s'))
llm_logger.addHandler(llm_file_handler)
llm_logger.setLevel(logging.INFO)

class TradingAgentCore:
    def __init__(self, system_prompt: str = "", model: str = None,
                 base_url: str = None, api_key: str = None,
                 api_key_pool: List[str] = None):

        # Use centralized config from config.py if not provided
        if model is None:
            model = LLM_MODEL
        if base_url is None:
            base_url = LLM_ENDPOINT
        if api_key is None:
            api_key = LLM_API_KEY

        self.model = model
        self.base_url = base_url
        self.api_key_pool = api_key_pool
        # Start at -1 so the first rotate call (before first turn) moves it to 0
        self.current_key_index = -1 if api_key_pool else 0
        
        # Determine initial API key
        effective_api_key = api_key
        if self.api_key_pool:
            # We will rotate immediately in run_turn, but initialize with first key for safety
            effective_api_key = self.api_key_pool[0]
            self.api_key = effective_api_key
        else:
            self.api_key = api_key

        if not effective_api_key:
            raise ValueError(f"LLM API Key not found. Please set NVIDIA_API_KEY or {model.upper() if model else 'LLM'}_API_KEY in .env")

        self.client = AsyncOpenAI(
            api_key=effective_api_key,
            base_url=base_url,
            timeout=120.0,
            max_retries=0 if self.api_key_pool else 3
        )

        
        # Tools initialized with core tools.
        self.available_schemas = list(CORE_TOOLS_SCHEMA)
        
        # Registry mapping tool names to async functions or standard functions
        # Core tools are handled natively.
        self.external_tool_registry = {}

        # 메시지 압축 관련
        self.last_compress_time = 0
        self.compress_interval = 3600  # 1시간마다 체크 (초 단위)
        self.max_tokens_threshold = 150000  # 토큰 임계값 (NVIDIA Context: 202752)

    def add_tool(self, schema: dict, handler: Any):
        """Registers a new tool schema and its corresponding execution handler.
        Skips registration if a tool with the same name already exists."""
        tool_name = schema["function"]["name"]
        # Deduplicate: skip if already registered
        for existing in self.available_schemas:
            if existing.get("function", {}).get("name") == tool_name:
                return
        self.available_schemas.append(schema)
        self.external_tool_registry[tool_name] = handler

    async def dispatch_tool(self, tool_name: str, arguments: dict) -> str:
        """Dispatches to either a core tool or an externally registered tool."""
        # Check core tools first
        for core in CORE_TOOLS_SCHEMA:
            if core["function"]["name"] == tool_name:
                return await dispatch_core_tool(tool_name, arguments)
        
        # Then check external
        if tool_name in self.external_tool_registry:
            handler = self.external_tool_registry[tool_name]
            if asyncio.iscoroutinefunction(handler):
                return await handler(tool_name, arguments)
            else:
                return handler(tool_name, arguments)
                
        return f"Unknown tool: {tool_name}"

    def _rotate_api_key(self):
        """Rotates to the next API key in the pool if available."""
        if not self.api_key_pool or len(self.api_key_pool) <= 1:
            return

        self.current_key_index = (self.current_key_index + 1) % len(self.api_key_pool)
        new_key = self.api_key_pool[self.current_key_index]
        self.api_key = new_key
        
        # Re-initialize client with new key
        self.client = AsyncOpenAI(
            api_key=new_key,
            base_url=self.base_url,
            timeout=120.0,
            max_retries=0 if self.api_key_pool else 3
        )
        msg = f"API Key Rotated for model {self.model}. Index: {self.current_key_index}"
        llm_logger.info(msg)
        print(f"[Agent] 🔄 {msg}") # Extra visibility in autonomous.log

    async def run_turn(self, user_input: str = None) -> str:
        """Runs the LLM loop asynchronously until it stops returning tool calls."""
        # Rotate key before each turn to spread load
        self._rotate_api_key()
        
        # Log request details for hybrid architecture and key rotation verification
        msg = f"LLM Turn Start - Model: {self.model}, KeyIndex: {self.current_key_index if self.api_key_pool else 'N/A'}"
        llm_logger.info(msg)
        print(f"[Agent] 🚀 {msg}")

        if user_input:
            self.messages.append({"role": "user", "content": user_input})

        while True:
            # 실시간 토큰 관리: 각 루프(도구 호출 전후)마다 압축 필요성을 체크합니다.
            # 이를 통해 긴 도구 호출 체인 중에도 토큰 한도 초과(BadRequestError)를 방지합니다.
            if await self._should_compress():
                await self._compress_messages()

            response = await self._call_llm_with_retry(
                model=self.model,
                messages=self.messages,
                tools=self.available_schemas if self.available_schemas else None,
                tool_choice="auto"
            )

            if response is None:
                return "LLM API Error: Failed after retries"

            message = response.choices[0].message
            # AsyncOpenAI choices message might need to be casted to dict or accessed via struct
            self.messages.append(message.model_dump(exclude_unset=True))

            if not getattr(message, "tool_calls", None):
                # LLM finished thinking & executing, returning final text
                return message.content

            # Handle tool calls
            for tool_call in message.tool_calls:
                tool_name = tool_call.function.name
                arguments_str = tool_call.function.arguments

                try:
                    arguments = json.loads(arguments_str)
                except json.JSONDecodeError:
                    arguments = {}

                print(f"[Agent] \ud234 \uc2e4\ud589: {tool_name}")
                tool_result_str = await self.dispatch_tool(tool_name, arguments)

                self.messages.append({
                    "role": "tool",
                    "tool_call_id": tool_call.id,
                    "name": tool_name,
                    "content": str(tool_result_str)
                })

    async def _estimate_tokens(self) -> int:
        """현재 메시지의 예상 토큰 수 추정 (대략: 1토큰 ≈ 4글자)"""
        total_chars = sum(len(str(msg.get("content", ""))) for msg in self.messages)
        return total_chars // 4  # 대략적인 추정

    async def _should_compress(self) -> bool:
        """압축이 필요한지 확인"""
        current_tokens = await self._estimate_tokens()
        
        # 토큰이 임계값을 확실히 넘었으면 시장 상황과 관계없이 압축 실행
        if current_tokens > self.max_tokens_threshold:
            print(f"[Agent] ⚠️ 토큰 임계값 초과 ({current_tokens} > {self.max_tokens_threshold}). 긴급 압축 실행.")
            return True

        # 시장이 열려있으면 일반적인 시간 기반 압축은 건너뜀
        if is_market_open("KR") or is_market_open("US"):
            return False

        # 일정 시간이 지났으면 압축
        elapsed = time.time() - self.last_compress_time
        return elapsed > self.compress_interval

    async def _compress_messages(self) -> None:
        """오래된 메시지를 요약해서 압축 (Serena 지능형 엔진 사용)"""
        if len(self.messages) <= 5:  # 충분한 기록이 없으면 압축 안 함
            return

        try:
            # system 프롬프트 유지
            system_msg = self.messages[0]
            # 최근 10개 메시지를 유지하려 시도하되, tool 호출 체인이 깨지지 않도록 조정
            recent_count = 10
            while recent_count < len(self.messages) - 1:
                boundary_idx = len(self.messages) - recent_count
                first_recent = self.messages[boundary_idx]
                
                # 'tool' 역할의 메시지로 시작하거나, 이전 메시지가 tool_calls를 가지고 있으면 뒤로 더 밀어야 함
                prev_msg = self.messages[boundary_idx - 1]
                if first_recent.get("role") == "tool" or (prev_msg.get("role") == "assistant" and prev_msg.get("tool_calls")):
                    recent_count += 1
                else:
                    break
            
            recent_messages = self.messages[-recent_count:]
            # 요약 대상 (system과 recent 제외한 모든 것)
            messages_to_compress = self.messages[1:-recent_count]

            if not messages_to_compress:
                return

            print(f"[Agent] 🗜️ Serena 지능형 압축 시작 (대상: {len(messages_to_compress)}개 메시지, 보존: {recent_count}개)")

            try:
                from core.serena_wrapper import serena_engine
                summary = await serena_engine.summarize_trading_context(messages_to_compress)
            except Exception as e:
                print(f"[Agent] Serena 압축 실패, 기본 요약으로 전환: {e}")
                # 기본 요약 프롬프트 (최소한의 컨텍스트를 위해 요약 대상 중 마지막 몇 개 포함)
                summary_prompt = "이전 대화 내용을 간결하게 요약해주세요. 현재 포지션과 중요 결정을 포함하세요."
                temp_messages = [system_msg, {"role": "user", "content": summary_prompt}] + messages_to_compress[-5:]
                
                sum_resp = await self.client.chat.completions.create(
                    model=self.model,
                    messages=temp_messages,
                    max_tokens=600
                )
                summary = sum_resp.choices[0].message.content if sum_resp else "요약 실패"

            # 새로운 메시지 구성
            self.messages = [
                system_msg,
                {"role": "system", "content": f"이전 대화 지능형 요약 (Serena): {summary}"},
                *recent_messages
            ]

            self.last_compress_time = time.time()
            print(f"[Agent] ✅ 지능형 압축 완료 (현재 메시지 {len(self.messages)}개)")

        except Exception as e:
            print(f"[Agent] ❌ 압축 프로세스 치명적 오류: {e}")
            if len(self.messages) > 20:
                self.messages = [self.messages[0]] + self.messages[-19:]

    async def _call_llm_with_retry(self, model: str = None, **kwargs):
        """
        Smart retry logic with exponential backoff for API calls.
        Handles rate limiting (429), server errors (5xx), and transient failures.
        """
        # max_tokens 설정으로 응답 길이 제한 (Context Length 초과 방지)
        kwargs["max_tokens"] = 2000  # 2000 토큰 제한 (NVIDIA API Context: 202752)

        # 실제 API 요청 크기 로깅
        messages = kwargs.get("messages", [])
        tools = kwargs.get("tools")
        if messages and tools:
            import json
            messages_str = json.dumps(messages, ensure_ascii=False)
            tools_str = json.dumps(tools, ensure_ascii=False)
            print(f"\n[Agent API Request]")
            print(f"  Messages: {len(messages)} items, {len(messages_str)} chars")
            print(f"  Tools: {len(tools)} items, {len(tools_str)} chars")
            print(f"  Total Input: ~{len(messages_str)//4 + len(tools_str)//4} tokens")

        # model 파라미터가 전달되면 사용, 아니면 self.model 사용
        if model is not None:
            kwargs["model"] = model
        elif self.model:
            kwargs["model"] = self.model
            
        max_attempts = max(LLM_MAX_RETRIES, len(self.api_key_pool) + 1) if self.api_key_pool else LLM_MAX_RETRIES

        for attempt in range(max_attempts):
            try:
                response = await self.client.chat.completions.create(**kwargs)
                return response
            except Exception as e:
                error_str = str(e)
                is_rate_limit = "429" in error_str or "rate limit" in error_str.lower()
                is_auth_error = "401" in error_str or "unauthorized" in error_str.lower()
                is_server_error = "5" in error_str and len(error_str) < 50  # 5xx errors
                is_timeout = "timeout" in error_str.lower() or "connection" in error_str.lower()

                rotated = False
                # Robustness: If rate limited or unauthorized, rotate key immediately and retry
                if (is_rate_limit or is_auth_error) and self.api_key_pool:
                    print(f"[Agent] ⚠️ Key issue detected ({error_str[:30]}). Rotating and retrying...")
                    self._rotate_api_key()
                    rotated = True

                # Calculate retry delay with exponential backoff
                delay = min(
                    LLM_RETRY_DELAY_BASE * (LLM_BACKOFF_MULTIPLIER ** attempt),
                    LLM_RETRY_DELAY_MAX
                )

                # Special handling for 429 - wait longer if we didn't rotate
                if is_rate_limit:
                    delay = min(delay * 2, LLM_RETRY_DELAY_MAX)
                    
                if rotated:
                    delay = 0  # Instant retry upon using a fresh key

                # Only retry on retryable errors
                if attempt < max_attempts - 1 and (is_rate_limit or is_auth_error or is_server_error or is_timeout):
                    llm_logger.warning(f"[LLM Retry] Attempt {attempt + 1}/{max_attempts} failed: {error_str[:100]}")
                    print(f"[LLM Retry] Attempt {attempt + 1}/{max_attempts} failed: {error_str[:100]}")
                    if delay > 0:
                        llm_logger.info(f"[LLM Retry] Waiting {delay}s before retry...")
                        await asyncio.sleep(delay)
                else:
                    llm_logger.error(f"[LLM Error] Final attempt failed: {error_str}")
                    print(f"[LLM Error] Final attempt failed: {error_str}")
                    return None

        return None
