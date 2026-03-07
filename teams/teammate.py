import asyncio
from core.agent import TradingAgentCore
from teams.task_manager import task_manager_instance, register_task_tools

from config import (get_api_key, TEAMMATE_MODEL, TEAMMATE_ENDPOINT, GEMINI_API_KEYS)

class TeammateAgent:
    def __init__(self, name: str, system_prompt: str, mcp_bridge=None):
        self.name = name
        # Use Gemini 3.1 Flash Lite with Key Rotation Pool for Teammates
        self.agent = TradingAgentCore(
            system_prompt=system_prompt,
            model=TEAMMATE_MODEL,
            base_url=TEAMMATE_ENDPOINT,
            api_key_pool=GEMINI_API_KEYS
        )
        
        # Register Task Update tools so the teammate can report its progress
        register_task_tools(self.agent)
        
        # If MCP is provided, register MCP tools
        self.mcp_bridge = mcp_bridge

    async def register_mcp_tools(self):
        if not self.mcp_bridge: return
        tools = await self.mcp_bridge.get_openai_tools()
        for tool in tools:
            # We bind the MCP call_tool function dynamically
            async def make_handler(tool_name):
                async def handler(name, args):
                    return await self.mcp_bridge.call_tool(tool_name, args)
                return handler
            
            handler = await make_handler(tool["function"]["name"])
            self.agent.add_tool(tool, handler)

    async def work_loop(self):
        """Continuously looks for pending tasks and executes them."""
        print(f"[{self.name}] Teammate started and waiting for tasks...")
        while True:
            # Atomic claim
            task = await task_manager_instance.get_claimable_task(self.name)
            if not task:
                await asyncio.sleep(2)
                continue
            
            task_id = task["id"]
            print(f"[{self.name}] Claimed Task {task_id}: {task['title']}")
            
            # Formulate prompt for this specific task
            prompt = f"Please complete the following task:\nTitle: {task['title']}\nDescription: {task['description']}\n\nWhen you are finished, use the TaskUpdate tool to set the status to 'completed' and provide the result."
            
            # Persistent Context (Option B): Carry over context between tasks for Serena intelligence
            # if self.agent.messages:
            #     self.agent.messages = [self.agent.messages[0]]
            
            # Run the LLM loop to complete the task
            result_str = await self.agent.run_turn(prompt)
            print(f"[{self.name}] Finished LLM execution for {task_id}.")
            
            # Safety fallback: if LLM didn't call TaskUpdate itself, we force it.
            current_status = await task_manager_instance.get_task(task_id)
            if '"status": "in progress"' in current_status:
                await task_manager_instance.update_task(task_id, status="completed", result=result_str)
            
            print(f"[{self.name}] Task {task_id} completed.")
