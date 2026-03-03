import asyncio
from typing import Dict, Any, List
import mcp.client.stdio
from mcp.client.session import ClientSession

class MCPBridge:
    def __init__(self, server_command: str, server_args: list[str], env: dict = None):
        self.server_command = server_command
        self.server_args = server_args
        self.env = env
        self.session: ClientSession = None
        self.stdio_ctx = None

    async def connect(self):
        """Connect to the MCP server via stdio."""
        server_params = mcp.client.stdio.StdioServerParameters(
            command=self.server_command,
            args=self.server_args,
            env=self.env
        )
        self.stdio_ctx = mcp.client.stdio.stdio_client(server_params)
        read, write = await self.stdio_ctx.__aenter__()
        
        self.session = ClientSession(read, write)
        await self.session.__aenter__()
        
        # Initialize the MCP connection
        await self.session.initialize()

    async def disconnect(self):
        """Disconnects from the MCP server."""
        if self.session:
            await self.session.__aexit__(None, None, None)
        if self.stdio_ctx:
            await self.stdio_ctx.__aexit__(None, None, None)

    async def get_openai_tools(self) -> List[Dict[str, Any]]:
        """Fetch tools from the MCP server and convert them to OpenAI function calling schema."""
        mcp_tools = await self.session.list_tools()
        openai_tools = []
        for tool in mcp_tools.tools:
            openai_tools.append({
                "type": "function",
                "function": {
                    "name": tool.name,
                    "description": tool.description,
                    "parameters": tool.inputSchema
                }
            })
        return openai_tools

    async def call_tool(self, name: str, arguments: dict):
        if not self.session:
            raise RuntimeError("MCP Session not established")
            
        import asyncio
        max_retries = 3
        for attempt in range(max_retries):
            try:
                result = await self.session.call_tool(name, arguments)
                if hasattr(result, "content") and isinstance(result.content, list) and len(result.content) > 0:
                    text_content = result.content[0].text
                    
                    # Intercept KIS "Non-Quota Exception" or rate limits
                    if "비-Quota 예외" in text_content or "api rate limit hit" in text_content.lower():
                        if attempt < max_retries - 1:
                            print(f"[Retry] Detected API limit/exception. Retrying in {(attempt+1)*3}s...")
                            await asyncio.sleep((attempt + 1) * 3)
                            continue
                    
                    return text_content
                return str(result)
            except Exception as e:
                err_str = str(e)
                if "비-Quota 예외" in err_str or "api rate limit hit" in err_str.lower():
                    if attempt < max_retries - 1:
                        print(f"[Retry] Caught API exception. Retrying in {(attempt+1)*3}s...")
                        await asyncio.sleep((attempt + 1) * 3)
                        continue
                # If it's the last attempt and we still have an exception, re-raise it.
                # The original instruction had a syntax error here, corrected to raise the exception.
                if attempt == max_retries - 1:
                    raise e
