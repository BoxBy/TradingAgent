import os
import json
import asyncio
from openai import AsyncOpenAI
from typing import List, Dict, Any, Optional

from config import get_api_key
from core.tools import CORE_TOOLS_SCHEMA, dispatch_core_tool

class TradingAgentCore:
    def __init__(self, system_prompt: str, model: str = "gemini-3-flash-preview", 
                 base_url: str = "https://generativelanguage.googleapis.com/v1beta/openai/"):
        
        # NOTE: Gemini endpoints supported via standard OPENAI client
        api_key = get_api_key("GOOGLE_API_KEY_1") or os.getenv("GEMINI_API_KEY")
        if not api_key:
            raise ValueError("GOOGLE_API_KEY_1 or GEMINI_API_KEY environment variable is missing.")

        self.client = AsyncOpenAI(
            api_key=api_key,
            base_url=base_url
        )
        self.model = model
        self.system_prompt = system_prompt
        self.messages = [
            {"role": "system", "content": self.system_prompt}
        ]
        
        # Tools initialized with core tools.
        self.available_schemas = list(CORE_TOOLS_SCHEMA)
        
        # Registry mapping tool names to async functions or standard functions
        # Core tools are handled natively.
        self.external_tool_registry = {}

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
                return dispatch_core_tool(tool_name, arguments)
        
        # Then check external
        if tool_name in self.external_tool_registry:
            handler = self.external_tool_registry[tool_name]
            if asyncio.iscoroutinefunction(handler):
                return await handler(tool_name, arguments)
            else:
                return handler(tool_name, arguments)
                
        return f"Unknown tool: {tool_name}"

    async def run_turn(self, user_input: str = None) -> str:
        """Runs the LLM loop asynchronously until it stops returning tool calls."""
        if user_input:
            self.messages.append({"role": "user", "content": user_input})

        while True:
            try:
                response = await self.client.chat.completions.create(
                    model=self.model,
                    messages=self.messages,
                    tools=self.available_schemas if self.available_schemas else None,
                    tool_choice="auto"
                )
            except Exception as e:
                return f"LLM API Error: {str(e)}"
            
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
