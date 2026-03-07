import asyncio
import uuid
import json
from typing import Dict, List, Any, Optional

class TaskManager:
    def __init__(self, mcp_bridge=None):
        # In-memory dictionary of tasks. Thread/async safe within a single event loop.
        self.mcp_bridge = mcp_bridge  # MCP 브릿지 저장
        self.tasks: Dict[str, dict] = {}
        self._lock = asyncio.Lock()

    async def create_task(self, title: str, description: str, blocked_by: List[str] = None) -> str:
        """Creates a new task and returns its ID."""
        task_id = str(uuid.uuid4())[:8]
        async with self._lock:
            self.tasks[task_id] = {
                "id": task_id,
                "title": title,
                "description": description,
                "status": "pending",
                "blocked_by": blocked_by or [],
                "result": None,
                "assignee": None
            }
        return task_id

    async def get_task(self, task_id: str) -> str:
        async with self._lock:
            task = self.tasks.get(task_id)
            if not task:
                return json.dumps({"error": f"Task {task_id} not found."})
            return json.dumps(task, indent=2)

    async def list_tasks(self) -> str:
        async with self._lock:
            return json.dumps(list(self.tasks.values()), indent=2)

    async def update_task(self, task_id: str, status: str = None, result: str = None, assignee: str = None) -> str:
        async with self._lock:
            if task_id not in self.tasks:
                return json.dumps({"error": f"Task {task_id} not found."})
            
            task = self.tasks[task_id]
            if status:
                task["status"] = status
            if result:
                task["result"] = result
            if assignee:
                task["assignee"] = assignee
            
            return json.dumps({"message": f"Task {task_id} updated successfully.", "task": task})

    async def get_claimable_task(self, agent_name: str) -> Optional[dict]:
        """Finds a pending task that has no uncompleted dependencies."""
        async with self._lock:
            # 빈 태스크 목록일 경우 로그 및 캐싱 시도
            if not self.tasks:
                if self.mcp_bridge:
                    # 캐싱으로 LLM 호출 시도 - Orchestrator가 멈춰지 않도록
                    try:
                        await self.mcp_bridge.call_tool("Tool_call", {})
                    except:
                        pass
                return None  # 태스크 없으면 None 반환

            for task_id, task in self.tasks.items():
                if task["status"] == "pending":
                    # Check if all blocked_by tasks are completed
                    can_claim = True
                    for dep_id in task["blocked_by"]:
                        dep_task = self.tasks.get(dep_id)
                        if not dep_task or dep_task["status"] != "completed":
                            can_claim = False
                            break

                    if can_claim:
                        task["status"] = "in progress"
                        task["assignee"] = agent_name
                        return dict(task)  # return a copy
        return None

# Singleton instance to be shared across orchestrator and teammates
task_manager_instance = TaskManager()

# --- Tool Wrappers ---

async def tool_task_create(tool_name: str, arguments: dict):
    task_id = await task_manager_instance.create_task(
        title=arguments.get("title"),
        description=arguments.get("description"),
        blocked_by=arguments.get("blocked_by", [])
    )
    return f"Task created successfully with ID: {task_id}"

async def tool_task_list(tool_name: str, arguments: dict):
    return await task_manager_instance.list_tasks()

async def tool_task_get(tool_name: str, arguments: dict):
    return await task_manager_instance.get_task(arguments.get("task_id"))

async def tool_task_update(tool_name: str, arguments: dict):
    return await task_manager_instance.update_task(
        task_id=arguments.get("task_id"),
        status=arguments.get("status"),
        result=arguments.get("result")
    )

TASK_TOOLS_SCHEMA = [
    {
        "type": "function",
        "function": {
            "name": "TaskCreate",
            "description": "Create a new core task for the team. Use this to break down complex objectives into smaller parallel tasks.",
            "parameters": {
                "type": "object",
                "properties": {
                    "title": {"type": "string", "description": "Short title of the task."},
                    "description": {"type": "string", "description": "Detailed description and expected output."},
                    "blocked_by": {
                        "type": "array", 
                        "items": {"type": "string"},
                        "description": "List of task IDs that must be completed before this task can start."
                    }
                },
                "required": ["title", "description"],
            },
        }
    },
    {
        "type": "function",
        "function": {
            "name": "TaskList",
            "description": "List all current tasks and their statuses.",
            "parameters": {"type": "object", "properties": {}},
        }
    },
    {
        "type": "function",
        "function": {
            "name": "TaskGet",
            "description": "Get detailed properties of a specific task.",
            "parameters": {
                "type": "object",
                "properties": {"task_id": {"type": "string"}},
                "required": ["task_id"],
            },
        }
    },
    {
        "type": "function",
        "function": {
            "name": "TaskUpdate",
            "description": "Update the status or result of a specific task.",
            "parameters": {
                "type": "object",
                "properties": {
                    "task_id": {"type": "string"},
                    "status": {"type": "string", "enum": ["pending", "in progress", "completed"]},
                    "result": {"type": "string", "description": "The final result or output of the task."}
                },
                "required": ["task_id", "status"],
            },
        }
    }
]

def register_task_tools(agent):
    """Registers task management tools to a TradingAgentCore instance."""
    agent.add_tool(TASK_TOOLS_SCHEMA[0], tool_task_create)
    agent.add_tool(TASK_TOOLS_SCHEMA[1], tool_task_list)
    agent.add_tool(TASK_TOOLS_SCHEMA[2], tool_task_get)
    agent.add_tool(TASK_TOOLS_SCHEMA[3], tool_task_update)
