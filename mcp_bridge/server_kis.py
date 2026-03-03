import asyncio
import sys
import os
import json
from mcp.server.stdio import stdio_server
from mcp.server import Server
import mcp.types as types

# Ensure we can import from the root
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from mcp_bridge.kis_trading import KISOfficialClient

# Initialize the official KIS client
kis = KISOfficialClient()

server = Server("kis-trading-server")

@server.list_tools()
async def handle_list_tools() -> list[types.Tool]:
    """List available tools for KIS trading."""
    return [
        types.Tool(
            name="get_buyable_cash",
            description="Get available KRW cash for domestic stock purchase.",
            inputSchema={
                "type": "object",
                "properties": {},
            },
        ),
        types.Tool(
            name="get_portfolio",
            description="Get current stock holdings for both KR and US markets.",
            inputSchema={
                "type": "object",
                "properties": {},
            },
        ),
        types.Tool(
            name="get_us_cash",
            description="Get available USD cash for overseas stock purchase.",
            inputSchema={"type": "object", "properties": {}},
        ),
        types.Tool(
            name="get_us_total_assets_krw",
            description="Get total overseas assets (portfolio + cash) in KRW.",
            inputSchema={"type": "object", "properties": {}},
        ),
        types.Tool(
            name="get_exchange_rate",
            description="Get current USD/KRW exchange rate.",
            inputSchema={"type": "object", "properties": {}},
        ),
        types.Tool(
            name="place_order",
            description="Place a buy or sell order for KR or US stocks.",
            inputSchema={
                "type": "object",
                "properties": {
                    "code": {"type": "string", "description": "Stock ticker code (e.g., '005930' or 'AAPL')"},
                    "qty": {"type": "integer", "description": "Quantity to buy/sell"},
                    "price": {"type": "number", "description": "Order price. Set to 0 for market order (KR only). US requires price > 0."},
                    "is_buy": {"type": "boolean", "description": "True for Buy, False for Sell"},
                    "market": {"type": "string", "description": "Market type: 'KR' or 'US'", "enum": ["KR", "US"]},
                },
                "required": ["code", "qty", "price", "is_buy", "market"],
            },
        ),
    ]

@server.call_tool()
async def handle_call_tool(
    name: str, arguments: dict | None
) -> list[types.TextContent]:
    """Handle tool execution requests."""
    try:
        if name == "get_buyable_cash":
            print("[MCP Server] Tool call: get_buyable_cash", file=sys.stderr)
            cash = kis.get_buyable_cash()
            return [types.TextContent(type="text", text=f"Buyable Cash (KRW): {cash:,}")]

        elif name == "get_us_cash":
            print("[MCP Server] Tool call: get_us_cash", file=sys.stderr)
            cash = kis.get_us_cash()
            return [types.TextContent(type="text", text=f"US Cash (USD): {cash:,}")]

        elif name == "get_us_total_assets_krw":
            print("[MCP Server] Tool call: get_us_total_assets_krw", file=sys.stderr)
            assets = kis.get_us_total_assets_krw()
            return [types.TextContent(type="text", text=f"US Total Assets (KRW): {assets:,}")]

        elif name == "get_exchange_rate":
            print("[MCP Server] Tool call: get_exchange_rate", file=sys.stderr)
            rate = kis.get_exchange_rate()
            return [types.TextContent(type="text", text=f"Exchange Rate (USD/KRW): {rate:,}")]
            
        elif name == "get_portfolio":
            print("[MCP Server] Tool call: get_portfolio", file=sys.stderr)
            pf = kis.get_portfolio()
            return [types.TextContent(type="text", text=json.dumps(pf, indent=2))]
            
        elif name == "place_order":
            success = kis.place_order(
                code=arguments["code"],
                qty=arguments["qty"],
                price=arguments["price"],
                is_buy=arguments["is_buy"],
                market=arguments["market"]
            )
            status = "Success" if success else "Failed"
            msg = f"Order {status}: {'Buy' if arguments['is_buy'] else 'Sell'} {arguments['qty']} of {arguments['code']}"
            print(f"[MCP Server] {msg}", file=sys.stderr)
            return [types.TextContent(type="text", text=msg)]
            
        else:
            raise ValueError(f"Unknown tool: {name}")
            
    except Exception as e:
        return [types.TextContent(type="text", text=f"Error executing tool {name}: {str(e)}")]

async def main():
    async with stdio_server() as (read_stream, write_stream):
        await server.run(
            read_stream,
            write_stream,
            server.create_initialization_options()
        )

if __name__ == "__main__":
    asyncio.run(main())
