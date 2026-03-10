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
                 api_key_pool: List[str] = None,
                 fallback_config: Optional[Dict[str, Any]] = None):


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
        self.fallback_config = fallback_config # {'model': ..., 'base_url': ..., 'api_key_pool': ...}
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
        # 250K TPM 대응을 위해 임계값을 대폭 낮춤 (기존 150,000 -> 40,000)
        # Lite 모델의 안정적 추론과 비용 절감을 위해 40k 근처에서 압축을 유도합니다.
        self.max_tokens_threshold = 40000 
        self.max_tool_loop_limit = 10 # 한 턴에서 최대 도구 호출 횟수 제한 (무한 루프 방지)
        
        # 메시지 히스토리 초기화
        self.messages = []
        if system_prompt:
            self.messages.append({"role": "system", "content": system_prompt})

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
        
        self.client = AsyncOpenAI(
            api_key=new_key,
            base_url=self.base_url,
            timeout=120.0,
            max_retries=0 if self.api_key_pool else 3
        )
        msg = f"API Key Rotated for model {self.model}. Index: {self.current_key_index}"
        llm_logger.info(msg)
        print(f"[Agent] 🔄 {msg}") # Extra visibility in autonomous.log

    def migrate_to_fallback(self):
        """Immediately migrates this agent instance to use its fallback configuration."""
        if not self.fallback_config:
            return False

        print(f"[Agent] 🛡️ EMERGENCY: Falling back to {self.fallback_config.get('model')}...")
        self.model = self.fallback_config.get("model")
        self.base_url = self.fallback_config.get("base_url")
        self.api_key_pool = self.fallback_config.get("api_key_pool")
        self.current_key_index = -1 if self.api_key_pool else 0
        
        # Reset client with fallback settings
        initial_key = self.api_key_pool[0] if self.api_key_pool else self.fallback_config.get("api_key")
        self.api_key = initial_key
        self.client = AsyncOpenAI(
            api_key=initial_key,
            base_url=self.base_url,
            timeout=120.0,
            max_retries=0 if self.api_key_pool else 3
        )
        # Clear fallback config to prevent nested fallbacks
        self.fallback_config = None 
        return True

    def _sanitize_messages(self, messages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Deeply sanitizes the message history to satisfy strict Gemini/Google API schemas."""
        import json as _json
        sanitized = []
        for msg in messages:
            # 1. Strip all None values at the top level
            m = {k: v for k, v in msg.items() if v is not None}
            
            # 2. Ensure content is present and is a string (if empty)
            if "content" not in m:
                m["content"] = ""
            
            # 3. Cleanup tool_calls and strip their None values
            if "tool_calls" in m:
                if not m["tool_calls"]:
                    del m["tool_calls"]
                else:
                    new_tc = []
                    for tc in m["tool_calls"]:
                        # Deep copy and preserve extra fields (like thought_signature)
                        clean_tc = {k: v for k, v in tc.items() if v is not None}
                        
                        # Fix for Gemini 3.1: Ensure we don't drop proprietary fields 
                        # that the API expects back in history (thought_signature, etc.)
                        for key in tc:
                            if key not in clean_tc and tc[key] is not None:
                                clean_tc[key] = tc[key]

                        fn = clean_tc.get("function")
                        if fn is not None:
                            clean_fn = {k: v for k, v in fn.items() if v is not None}
                            args = clean_fn.get("arguments")
                            if args is None:
                                clean_fn["arguments"] = "{}"
                            elif isinstance(args, dict):
                                clean_fn["arguments"] = _json.dumps(args, ensure_ascii=False)
                            clean_tc["function"] = clean_fn
                        new_tc.append(clean_tc)
                    m["tool_calls"] = new_tc
            
            # 4. Handle 'tool' role messages specific logic
            if m.get("role") == "tool":
                if not m.get("content"):
                    m["content"] = "Tool executed with no return value."
            
            sanitized.append(m)
        return sanitized

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

        loop_count = 0
        while True:
            loop_count += 1
            if loop_count > self.max_tool_loop_limit:
                print(f"[Agent] ⚠️ 도구 호출 루프 한도 초과 ({self.max_tool_loop_limit}). 루프를 강제 종료합니다.")
                return "Error: Tool call loop limit reached."

            # 실시간 토큰 관리
            if await self._should_compress():
                await self._compress_messages()

            response = await self._call_llm_with_retry(
                messages=self.messages,
                tools=self.available_schemas if self.available_schemas else None,
                tool_choice="auto"
            )


            if response is None:
                return "LLM API Error: Failed after retries"

            message = response.choices[0].message
            # Sanitize immediately before storing to prevent null struct errors on fallback
            msg_dump = message.model_dump(exclude_unset=True)
            sanitized_msg = self._sanitize_messages([msg_dump])[0]
            self.messages.append(sanitized_msg)

            if not getattr(message, "tool_calls", None) or len(message.tool_calls) == 0:
                # LLM finished thinking & executing, returning final text
                return message.content


            # Handle tool calls
            for tool_call in message.tool_calls:
                original_tool_name = tool_call.function.name
                # Strip namespace prefix if present (e.g., 'default_api:TaskCreate' -> 'TaskCreate')
                tool_name = original_tool_name.split(':')[-1] if ':' in original_tool_name else original_tool_name
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
        """현재 메시지의 예상 토큰 수 추정 (보수적 추정: 1토큰 ≈ 3글자)"""
        # 도구 호출(tool_calls) 데이터도 포함하여 계산하도록 강화
        total_chars = 0
        for msg in self.messages:
            total_chars += len(str(msg.get("content", "")))
            if "tool_calls" in msg:
                total_chars += len(str(msg["tool_calls"]))
        
        # 한국어와 JSON 구조를 고려하여 3으로 나눔 (기존 4보다 더 보수적)
        return total_chars // 3 

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
            # 최근 10개 메시지를 유지하려 시도하되, 항상 'user' 역할로 시작하도록 조정 (Gemini 스키마 준수)
            recent_count = 10
            while recent_count < len(self.messages) - 1:
                boundary_idx = len(self.messages) - recent_count
                if boundary_idx <= 1: # 시스템 메시지 바로 다음이면 중단
                    break
                
                # Gemini 호환성을 위해 새로운 세션의 시작은 반드시 'user'여야 안전함
                if self.messages[boundary_idx].get("role") == "user":
                    break
                recent_count += 1
            
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
                summary_prompt = "Summarize the previous conversation concisely. Focus on current positions, important decisions, and strategic intent."
                temp_messages = [system_msg, {"role": "user", "content": summary_prompt}] + messages_to_compress[-5:]
                
                sum_resp = await self.client.chat.completions.create(
                    model=self.model,
                    messages=temp_messages,
                    max_tokens=600
                )
                summary = sum_resp.choices[0].message.content if sum_resp else "요약 실패"

            # 새로운 메시지 구성
            # 새로운 메시지 구성: 첫 번째 시스템 메시지에 요약 내용을 통합하여 순서 위반 방지
            updated_system_content = system_msg.get("content", "") + f"\n\n[이전 대화 지능형 요약 (Serena)]\n{summary}"
            self.messages = [
                {"role": "system", "content": updated_system_content},
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

        max_attempts = max(LLM_MAX_RETRIES, len(self.api_key_pool) + 1) if self.api_key_pool else LLM_MAX_RETRIES

        for attempt in range(max_attempts):
            # API 호출 직전 전역 새니타이징 및 모델 확정
            current_messages = self._sanitize_messages(kwargs.get("messages", []))
            current_model = model if model else self.model
            
            call_kwargs = dict(kwargs)
            call_kwargs["messages"] = current_messages
            call_kwargs["model"] = current_model

            try:
                response = await self.client.chat.completions.create(**call_kwargs)
                return response
            except Exception as e:
                error_str = str(e)
                is_rate_limit = "429" in error_str or "rate limit" in error_str.lower()
                is_auth_error = "401" in error_str or "unauthorized" in error_str.lower()
                is_server_error = "500" in error_str or "502" in error_str or "503" in error_str or "504" in error_str or ("5" in error_str and "status" in error_str)
                is_timeout = "timeout" in error_str.lower() or "connection" in error_str.lower() or "deadline" in error_str.lower()
                is_not_found = "404" in error_str or "not found" in error_str.lower()


                rotated = False
                # Robustness: If rate limited or unauthorized, rotate key immediately and retry
                if (is_rate_limit or is_auth_error) and self.api_key_pool:
                    print(f"[Agent] ⚠️ Key issue detected ({error_str[:30]}). Rotating and retrying...")
                    self._rotate_api_key()
                    rotated = True

                # Calculate retry delay with exponential backoff + jitter
                import random
                jitter = random.uniform(0.5, 1.5)
                delay = min(
                    LLM_RETRY_DELAY_BASE * (LLM_BACKOFF_MULTIPLIER ** attempt) * jitter,
                    LLM_RETRY_DELAY_MAX
                )

                # Special handling for 429 - wait significantly longer if we hit quota
                if is_rate_limit:
                    delay = max(delay, 5.0) # minimum 5s for 429
                    
                # Even if we rotate, adding a small delay prevents "key rotation storms"
                # where 20 keys are burned in 1 second.
                if rotated:
                    delay = max(delay, 1.0) # minimum 1s upon rotation

                # Only retry on retryable errors
                if attempt < max_attempts - 1 and (is_rate_limit or is_auth_error or is_server_error or is_timeout):
                    llm_logger.warning(f"[LLM Retry] Attempt {attempt + 1}/{max_attempts} failed: {error_str[:100]}. Waiting {delay:.1f}s...")
                    print(f"[LLM Retry] Attempt {attempt + 1}/{max_attempts} failed: {error_str[:100]}. Waiting {delay:.1f}s...")
                    await asyncio.sleep(delay)
                elif self.fallback_config and self.migrate_to_fallback():
                    # 404나 타임아웃 등 모든 실패 상황에서 폴백 설정이 있으면 즉시 전환
                    print("[Agent] 🚀 Retrying turn with Fallback Engine...")
                    # 재귀 호출 시 model 파라미터를 None으로 넘겨서 새로 바뀐 self.model을 쓰게 함
                    if "model" in kwargs: del kwargs["model"]
                    return await self._call_llm_with_retry(**kwargs)
                else:
                    llm_logger.error(f"[LLM Error] Final attempt failed: {error_str}")
                    print(f"[LLM Error] Final attempt failed: {error_str}")
                    return None


        return None
