import asyncio
import sys
import json
from mcp_bridge.client import MCPBridge
from config import KIS_APP_KEY, KIS_APP_SECRET, KIS_ACCOUNT_NO, MOCK_TRADING

async def main():
    print(f"Connecting to KIS MCP (Mock: {MOCK_TRADING})...")
    bridge = MCPBridge(
        server_command="docker",
        server_args=["run", "-i", "--rm",
                     "-e", f"KIS_APP_KEY={KIS_APP_KEY}",
                     "-e", f"KIS_APP_SECRET={KIS_APP_SECRET}",
                     "-e", f"KIS_ACCOUNT_NO={KIS_ACCOUNT_NO}",
                     "-e", f"KIS_PAPER_TRADING={MOCK_TRADING}",
                     "kis-mcp"]
    )
    
    await bridge.connect()
    tools = await bridge.get_openai_tools()
    print(json.dumps(tools, indent=2, ensure_ascii=False))
    await bridge.disconnect()

if __name__ == "__main__":
    asyncio.run(main())
