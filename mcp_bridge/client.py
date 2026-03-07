import asyncio
import os # Added: required for environ
from typing import Dict, Any, List

# Simple MCP bridge that only supports KIS server (no mcp package dependency)
class MCPBridge:
    def __init__(self, server_command: str, server_args: list[str], env: dict = None):
        self.server_command = server_command
        self.server_args = server_args
        self.env = env
        self.kis_process = None  # Fixed: initialize to None
        self.stdio_ctx = None

    async def connect(self):
        """Connect to KIS MCP server via stdio."""
        # Use python's stdio directly (mcp package causes issues)
        import subprocess

        # Prepare environment
        server_env = self.env if self.env else os.environ.copy()

        # Start KIS MCP server as subprocess
        # asyncio.create_subprocess_exec takes program as first positional, then *args
        process = await asyncio.create_subprocess_exec(
            self.server_command,
            *self.server_args,
            env=server_env,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE
        )

        # Store process reference
        self.kis_process = process
        self.stdio_ctx = None

        print(f"[MCP Bridge] KIS MCP server started (PID: {process.pid})")

    async def disconnect(self):
        """Disconnect from KIS MCP server."""
        if self.kis_process:
            print("[MCP Bridge] Terminating KIS MCP server...")
            self.kis_process.terminate()
            try:
                await asyncio.wait_for(self.kis_process, timeout=5)
            except asyncio.TimeoutError:
                self.kis_process.kill()
            self.kis_process = None

    async def get_openai_tools(self) -> List[Dict[str, Any]]:
        """Not implemented - use core tools only"""
        return []

    async def call_tool(self, name: str, arguments: dict):
        """Not implemented - use core tools only"""
        return f"Tool {name} not available"
