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

# LLM API 로깅 설정 - 일별 로테이트 (30일 보관)
from logging.handlers import TimedRotatingFileHandler

llm_logger = logging.getLogger('llm_api')
llm_file_handler = TimedRotatingFileHandler(
    'logs/llm_api.log', when='midnight', interval=1, backupCount=30, encoding='utf-8'
)
llm_file_handler.setFormatter(logging.Formatter('%(asctime)s - %(levelname)s - %(message)s'))
llm_file_handler.suffix = '%Y-%m-%d'
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
        if api_key_pool is None:
            from config import GEMINI_API_KEYS
            # Gemini 키풀은 Gemini endpoint에서만 사용 (NVIDIA에 Google 키 전송 방지)
            if GEMINI_API_KEYS and "generativelanguage.googleapis.com" in (base_url or ""):
                api_key_pool = GEMINI_API_KEYS

        if fallback_config is None:
            from config import get_fallback_config
            fallback_config = get_fallback_config()

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
        # 모델 컨텍스트 한도(262,144)의 ~57% 수준에서 압축 트리거
        # 반복 압축 루프가 실제로 토큰을 줄이므로, 충분한 컨텍스트를 유지하면서 안전 마진 확보
        self.max_tokens_threshold = 150000 
        self.max_tool_loop_limit = 25 # 한 턴에서 최대 도구 호출 횟수 제한 (무한 루프 방지)
        
        # 메시지 히스토리 초기화
        self.messages = []
        self.system_prompt = system_prompt
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

    def update_system_prompt(self, new_prompt: str):
        """Updates the system prompt in both the message history and the stored reference."""
        self.system_prompt = new_prompt
        if not self.messages or self.messages[0].get("role") != "system":
            self.messages.insert(0, {"role": "system", "content": new_prompt})
        else:
            self.messages[0]["content"] = new_prompt
        # llm_logger.info(f"[Agent] System prompt updated: {new_prompt[:100]}...")

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
        """Migrates to the next model in the fallback chain."""
        if not self.fallback_config:
            return False

        print(f"[Agent] 🛡️ Fallback: {self.model} → {self.fallback_config.get('model')}...")
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
        
        # Gemini 호환: thought_signature 주입 + tool role name 보장
        if "gemini" in self.model.lower() or "gemma" in self.model.lower():
            for msg in self.messages:
                if "tool_calls" in msg:
                    for tc in msg["tool_calls"]:
                        if "thought_signature" not in tc:
                            tc["thought_signature"] = "fallback_dummy_signature_123"
                # Ensure tool role messages have a non-empty 'name' for Gemini
                if msg.get("role") == "tool" and not msg.get("name"):
                    msg["name"] = msg.get("tool_call_id", "unknown_tool")

        # Remove orphaned tool messages (tool_call_id not matching any assistant tool_call)
        valid_tool_call_ids = set()
        for msg in self.messages:
            if msg.get("role") == "assistant" and "tool_calls" in msg:
                for tc in msg["tool_calls"]:
                    tc_id = tc.get("id")
                    if tc_id:
                        valid_tool_call_ids.add(tc_id)
        self.messages = [
            msg for msg in self.messages
            if not (msg.get("role") == "tool" and msg.get("tool_call_id") not in valid_tool_call_ids)
        ]

        # 체인의 다음 fallback으로 이동 (없으면 None → 마지막 단계)
        self.fallback_config = self.fallback_config.get("next_fallback")
        return True

    def _sanitize_messages(self, messages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Deeply sanitizes the message history to satisfy strict Gemini/Google API schemas."""
        import json as _json
        sanitized = []
        for msg in messages:
            # 1. Strip all None values at the top level
            m = {k: v for k, v in msg.items() if v is not None}
            
            # 1.5. 🐛 Fix: Gemini contents[].parts[] 에러 방지 
            # content가 빈 문자열이거나 whitespace-only면 명시적 빈 문자열로 보정
            # (Gemini는 parts에 빈 text를 거부함)
            content = m.get("content")
            if content is not None and isinstance(content, str) and not content.strip():
                m["content"] = " "  # 최소 1자 공백
            elif content is None and m.get("role") in ("system", "user", "assistant"):
                m["content"] = " "
            
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
                # Gemini API requires a non-empty 'name' for function_response.
                # If missing/empty, derive from tool_call_id or use a default.
                if not m.get("name"):
                    m["name"] = m.get("tool_call_id", "unknown_tool")
            
            sanitized.append(m)
        return sanitized

    async def run_turn(self, user_input: str = None, json_mode: bool = False) -> str:
        """Runs the LLM loop asynchronously until it stops returning tool calls.
        If json_mode is True, the final output is forced to be a JSON object.
        """
        # Rotate key before each turn to spread load
        self._rotate_api_key()
        
        # Log request details for hybrid architecture and key rotation verification
        msg = f"LLM Turn Start - Model: {self.model}, KeyIndex: {self.current_key_index if self.api_key_pool else 'N/A'}"
        llm_logger.info(msg)
        print(f"[Agent] 🚀 {msg}")

        if user_input:
            self.messages.append({"role": "user", "content": user_input})

        loop_count = 0
        recent_tool_calls = []  # Detect actual infinite loops (same tool+args repeated)
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
                tool_choice="auto",
                json_mode=json_mode
            )


            if response is None:
                # Retry once with simplified history: system prompt + last 3 user messages only
                print("[Agent] 🔄 API returned None — retrying with simplified message history...")
                simplified = [m for m in self.messages if m.get("role") == "system"]
                user_msgs = [m for m in self.messages if m.get("role") == "user"]
                simplified.extend(user_msgs[-3:])
                if not simplified or simplified[0].get("role") != "system":
                    simplified.insert(0, {"role": "system", "content": self.system_prompt})

                response = await self._call_llm_with_retry(
                    messages=simplified,
                    tools=self.available_schemas if self.available_schemas else None,
                    tool_choice="auto",
                    json_mode=json_mode
                )
                if response is None:
                    return "LLM API Error: Failed after retries"
                print("[Agent] ✅ Simplified history retry succeeded")

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
                original_tool_name = tool_call.function.name or ""
                # Strip namespace prefix if present (e.g., 'default_api:TaskCreate' -> 'TaskCreate')
                tool_name = original_tool_name.split(':')[-1] if ':' in original_tool_name else original_tool_name
                
                # Guard: if tool_name is empty, use a fallback to prevent Gemini API errors
                if not tool_name:
                    tool_name = f"unknown_tool_{tool_call.id[:8] if tool_call.id else 'no_id'}"
                    print(f"[Agent] ⚠️ 빈 도구 이름 감지, fallback 사용: {tool_name}")
                
                arguments_str = tool_call.function.arguments

                try:
                    arguments = json.loads(arguments_str)
                except json.JSONDecodeError:
                    arguments = {}

                # --- Enhanced Infinite Loop Detection ---
                # Build a fingerprint: tool_name + sorted args (for same-request detection)
                try:
                    args_fingerprint = f"{tool_name}:{json.dumps(arguments, sort_keys=True)}"
                except:
                    args_fingerprint = f"{tool_name}:{arguments_str}"
                recent_tool_calls.append(args_fingerprint)

                # Dangerous tools: block after 2 consecutive identical calls
                dangerous_tools = {"place_order", "place_sell_order", "buy_stock", "sell_stock"}
                if tool_name in dangerous_tools and len(recent_tool_calls) >= 2:
                    last_2 = recent_tool_calls[-2:]
                    if len(set(last_2)) == 1:
                        print(f"[Agent] 🚨 위험 도구 무한 루프 감지: '{tool_name}' 2회 연속 동일 호출. 강제 종료.")
                        self.messages.append({
                            "role": "tool",
                            "tool_call_id": tool_call.id,
                            "name": tool_name,
                            "content": f"⚠️ BLOCKED: '{tool_name}' was called twice with identical arguments. This looks like a loop. STOP calling this tool and proceed to a different action or return your analysis."
                        })
                        continue

                # Same tool 3+ times in a row → inject warning to steer LLM away
                if len(recent_tool_calls) >= 3:
                    last_3 = recent_tool_calls[-3:]
                    if all(tcn.startswith(f"{tool_name}:") for tcn in last_3):
                        print(f"[Agent] ⚠️ 반복 호출 감지: '{tool_name}' 3회 연속. 경고 메시지 주입.")
                        self.messages.append({
                            "role": "tool",
                            "tool_call_id": tool_call.id,
                            "name": tool_name,
                            "content": f"⚠️ You have called '{tool_name}' 3 times in a row. This is likely a loop. STOP repeating this call. Use a DIFFERENT tool or return your final response now."
                        })
                        continue

                # Same exact request 4+ times → hard kill
                if len(recent_tool_calls) >= 4:
                    last_4 = recent_tool_calls[-4:]
                    if len(set(last_4)) == 1:
                        print(f"[Agent] ⚠️ 무한 루프 감지: '{tool_name}' 4회 연속 동일 호출. 강제 종료.")
                        return f"Error: Infinite tool call loop detected for '{tool_name}' with identical arguments."

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
        """오래된 메시지를 요약해서 압축. 반복 압축으로 항상 토큰 한도 이내 보장."""
        if len(self.messages) <= 5:
            return

        try:
            # 보존할 최근 메시지 수 (시작값). 반복마다 줄여가며 토큰을 맞춤.
            recent_count = min(10, len(self.messages) - 2)

            while True:
                # Gemini 호환: 보존 구간의 첫 메시지가 'user'로 시작하도록 조정
                # 단, recent_count의 50%까지만 증가 허용 (압축 무력화 방지)
                adjusted_recent = recent_count
                max_adjusted = min(recent_count + max(recent_count // 2, 2), len(self.messages) - 2)
                while adjusted_recent < max_adjusted:
                    boundary_idx = len(self.messages) - adjusted_recent
                    if boundary_idx <= 1:
                        break
                    if self.messages[boundary_idx].get("role") == "user":
                        break
                    adjusted_recent += 1

                recent_messages = self.messages[-adjusted_recent:]
                messages_to_compress = self.messages[1:-adjusted_recent] if adjusted_recent < len(self.messages) - 1 else []

                if not messages_to_compress:
                    # 경계 조정으로 압축 대상이 비어버림 → recent_count 축소 후 재시도
                    post_tokens = await self._estimate_tokens()
                    if post_tokens <= self.max_tokens_threshold:
                        self.messages[0] = {"role": "system", "content": self.system_prompt}
                        break
                    new_recent = max(recent_count // 2, 3)
                    if new_recent >= recent_count:
                        print(f"[Agent] ⚠️ 경계 조정으로 압축 불가. 강제 최소화.")
                        self.messages = [
                            {"role": "system", "content": self.system_prompt},
                            {"role": "user", "content": "[이전 대화 컨텍스트가 압축되었습니다. 계속 진행합니다.]"}
                        ]
                        break
                    print(f"[Agent] 🔄 경계 조정 스킵 — 보존 축소 ({recent_count} → {new_recent}) 재시도")
                    recent_count = new_recent
                    continue

                print(f"[Agent] 🗜️ 압축 시작 (대상: {len(messages_to_compress)}개 메시지, 보존: {adjusted_recent}개)")

                try:
                    from core.serena_wrapper import serena_engine
                    summary = await serena_engine.summarize_trading_context(messages_to_compress)
                except Exception as e:
                    print(f"[Agent] Serena 압축 실패, 기본 요약으로 전환: {e}")
                    summary_prompt = "Summarize the previous conversation concisely. Focus on current positions, important decisions, and strategic intent."
                    temp_messages = [
                        {"role": "system", "content": self.system_prompt},
                        {"role": "user", "content": summary_prompt}
                    ] + messages_to_compress[-5:]
                    sum_resp = await self._call_llm_with_retry(messages=temp_messages)
                    if sum_resp is None:
                        summary = "요약 실패"
                    else:
                        summary = sum_resp.choices[0].message.content if sum_resp.choices else "요약 실패"

                # 핵심 수정: 원본 시스템 프롬프트에서 재구성 (누적 방지)
                updated_system_content = self.system_prompt + f"\n\n[이전 대화 요약]\n{summary}"
                self.messages = [
                    {"role": "system", "content": updated_system_content},
                    *recent_messages
                ]

                # 압축 후 토큰 재측정
                post_tokens = await self._estimate_tokens()
                print(f"[Agent] 📊 압축 후 토큰: {post_tokens} (한도: {self.max_tokens_threshold})")

                if post_tokens <= self.max_tokens_threshold:
                    break  # 성공 — 한도 이내

                # 아직 초과: recent_count를 줄여서 재시도
                new_recent = max(recent_count // 2, 3)
                if new_recent >= recent_count:
                    # 더 이상 줄일 수 없음 — 최소 메시지만 유지
                    print(f"[Agent] ⚠️ 최소 보존으로도 한도 초과. 시스템 프롬프트 요약만 보존.")
                    self.messages = [
                        {"role": "system", "content": self.system_prompt},
                        {"role": "user", "content": f"[이전 대화 요약]\n{summary}"}
                    ]
                    break
                print(f"[Agent] 🔄 토큰 초과 — 보존 메시지 축소 ({recent_count} → {new_recent})하여 재압축")
                recent_count = new_recent

            self.last_compress_time = time.time()
            post_tokens = await self._estimate_tokens()
            print(f"[Agent] ✅ 압축 완료 (메시지 {len(self.messages)}개, 토큰 ~{post_tokens})")

        except Exception as e:
            import traceback as _tb
            print(f"[Agent] ❌ 압축 치명적 오류: {e}")
            from core.error_escalation import escalate_error
            escalate_error(
                '압축 치명적 오류',
                f'Message compression failed critically: {e}',
                _tb.format_exc()
            )
            # 비상 복구: 원본 시스템 프롬프트 + 최근 메시지만
            self.messages = [{"role": "system", "content": self.system_prompt}] + self.messages[-5:]

    async def _call_llm_with_retry(self, model: str = None, json_mode: bool = False, **kwargs):
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
        consecutive_503 = 0  # 503 연속 카운터 — 임계치 초과 시 즉시 폴백

        for attempt in range(max_attempts):
            # API 호출 직전 전역 새니타이징 및 모델 확정
            current_messages = self._sanitize_messages(kwargs.get("messages", []))
            current_model = model if model else self.model
            
            # Gemini/Gemma 전용: function_response.name 빈값 2차 검증
            # _sanitize_messages이 처리하지만, Gemini API가 특히 엄격하므로 추가 방어
            is_google_model = "gemini" in current_model.lower() or "gemma" in current_model.lower()
            if is_google_model:
                for i, msg in enumerate(current_messages):
                    if msg.get("role") == "tool":
                        if not msg.get("name"):
                            msg["name"] = msg.get("tool_call_id", "unknown_tool")
                            print(f"[Agent] 🔧 Gemini 검증: messages[{i}] 빈 name → {msg['name']}")
                    # assistant 메시지의 tool_calls에서 빈 function.name도 검증
                    if "tool_calls" in msg:
                        for tc in msg["tool_calls"]:
                            fn = tc.get("function", {})
                            if fn and not fn.get("name"):
                                fn["name"] = "unknown_function"
                                print(f"[Agent] 🔧 Gemini 검증: tool_calls 빈 function.name → unknown_function")
            
            # In-place 정제: self.messages에도 동일한 정제 적용 (다음 호출 시 누적 방지)
            if kwargs.get("messages") is self.messages:
                self.messages = self._sanitize_messages(self.messages)
            
            call_kwargs = dict(kwargs)
            call_kwargs["messages"] = current_messages
            call_kwargs["model"] = current_model

            if json_mode:
                call_kwargs["response_format"] = {"type": "json_object"}

            try:
                llm_logger.info(f"API Call #{attempt+1} - Model: {current_model}")
                response = await self.client.chat.completions.create(**call_kwargs)
                return response
            except Exception as e:
                error_str = str(e)
                is_rate_limit = "429" in error_str or "rate limit" in error_str.lower()
                is_auth_error = "401" in error_str or "unauthorized" in error_str.lower()
                is_server_error = "500" in error_str or "502" in error_str or "503" in error_str or "504" in error_str or ("5" in error_str and "status" in error_str)
                is_timeout = "timeout" in error_str.lower() or "connection" in error_str.lower() or "deadline" in error_str.lower()
                is_not_found = "404" in error_str or "not found" in error_str.lower()
                is_gemini_format_error = "function_response.name" in error_str or "function_call.name" in error_str or "GenerateContentRequest.contents" in error_str


                rotated = False
                # 503 연속 카운터 업데이트
                if "503" in error_str:
                    consecutive_503 += 1
                else:
                    consecutive_503 = 0

                # 🔥 503 폴백 최적화: 3회 연속 503 시 즉시 폴백 체인으로 전환
                # (키 로테이션은 429에만 효과 있고, 503은 서버 과부하라 키와 무관)
                if consecutive_503 >= 3 and self.fallback_config and self.migrate_to_fallback():
                    print(f"[Agent] 🔀 503 연속 {consecutive_503}회 감지 — 즉시 폴백 모델로 전환합니다")
                    llm_logger.warning(f"[LLM Fallback] 503 x{consecutive_503} → 폴백 체인 활성화")
                    if "model" in kwargs: del kwargs["model"]
                    return await self._call_llm_with_retry(**kwargs)

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

                # Gemini format error: 강제 in-place 정제 후 1회 재시도
                if is_gemini_format_error:
                    print(f"[Agent] 🔧 Gemini 포맷 에러 감지. self.messages 강제 정제 후 재시도...")
                    self.messages = self._sanitize_messages(self.messages)
                    for msg in self.messages:
                        if msg.get("role") == "tool" and not msg.get("name"):
                            msg["name"] = msg.get("tool_call_id", "unknown_tool")

                # Only retry on retryable errors
                # 🐛 Fix: Gemini 400 contents[].parts[] 에러는 sanitize로 해결 안 되면 
                # 3회까지만 재시도하고 바로 fallback으로 전환 (무한 루프 방지)
                is_client_400 = "400" in error_str and not is_rate_limit and not is_auth_error
                
                if attempt < max_attempts - 1 and (is_rate_limit or is_auth_error or is_server_error or is_timeout or is_gemini_format_error):
                    # 400 client error: sanitize 최대 3회까지만, 그 후엔 fallback
                    if is_client_400 and is_gemini_format_error and attempt >= 3:
                        if self.fallback_config and self.migrate_to_fallback():
                            print(f"[Agent] 🚀 Gemini 400 format error {attempt+1}회 — sanitize 실패, fallback 전환")
                            if "model" in kwargs: del kwargs["model"]
                            return await self._call_llm_with_retry(**kwargs)
                    
                    llm_logger.warning(f"[LLM Retry] Attempt {attempt + 1}/{max_attempts} failed: {error_str[:500]}. Waiting {delay:.1f}s...")
                    print(f"[LLM Retry] Attempt {attempt + 1}/{max_attempts} failed: {error_str[:500]}. Waiting {delay:.1f}s...")
                    await asyncio.sleep(delay)
                elif self.fallback_config and self.migrate_to_fallback():
                    # 404나 타임아웃 등 모든 실패 상황에서 폴백 설정이 있으면 즉시 전환
                    print("[Agent] 🚀 Retrying turn with Fallback Engine...")
                    # 재귀 호출 시 model 파라미터를 None으로 넘겨서 새로 바뀐 self.model을 쓰게 함
                    if "model" in kwargs: del kwargs["model"]
                    return await self._call_llm_with_retry(**kwargs)
                else:
                    import traceback as _tb
                    llm_logger.error(f"[LLM Error] Final attempt failed: {error_str}")
                    print(f"[LLM Error] Final attempt failed: {error_str}")
                    from core.error_escalation import escalate_error
                    escalate_error(
                        'LLM Error Final attempt failed',
                        f'LLM call failed after all retries: {error_str[:300]}',
                        _tb.format_exc()
                    )
                    return None


        return None
