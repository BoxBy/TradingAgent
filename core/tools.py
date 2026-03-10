import os
import subprocess
from typing import Dict, Any, List

def execute_bash(command: str) -> str:
    """Executes a bash command and returns the output explicitly."""
    # Safety Check for destructive commands could go here
    try:
        result = subprocess.run(
            command, shell=True, check=True, capture_output=True, text=True, timeout=120
        )
        return result.stdout
    except subprocess.CalledProcessError as e:
        return f"Error executing command:\nSTDOUT:\n{e.stdout}\nSTDERR:\n{e.stderr}"
    except subprocess.TimeoutExpired as e:
        return f"Command execution timed out: {e}"

def read_file(filepath: str) -> str:
    """Reads the content of a file."""
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            return f.read()
    except Exception as e:
        return f"Error reading file {filepath}: {str(e)}"

def write_file(filepath: str, content: str) -> str:
    """Writes content to a file, overwriting existing content."""
    try:
        # Create directories if they do not exist
        os.makedirs(os.path.dirname(os.path.abspath(filepath)), exist_ok=True)
        with open(filepath, "w", encoding="utf-8") as f:
            f.write(content)
        return f"Successfully wrote to {filepath}"
    except Exception as e:
        return f"Error writing to file {filepath}: {str(e)}"

def edit_file(filepath: str, old_string: str, new_string: str) -> str:
    """Replaces all occurrences of old_string with new_string in a file."""
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            content = f.read()
        
        if old_string not in content:
            return f"Error: '{old_string}' not found in {filepath}."
            
        content = content.replace(old_string, new_string)
        
        with open(filepath, "w", encoding="utf-8") as f:
            f.write(content)
        return f"Successfully edited {filepath}"
    except Exception as e:
        return f"Error editing file {filepath}: {str(e)}"

# Define the JSON schemas for the LLM tool calling
CORE_TOOLS_SCHEMA = [
    {
        "type": "function",
        "function": {
            "name": "execute_bash",
            "description": "Execute a bash command on the system and return its output. This is the primary way to interact with the system.",
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {"type": "string", "description": "The bash command to execute."}
                },
                "required": ["command"],
            },
        }
    },
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "Read the contents of a specific file.",
            "parameters": {
                "type": "object",
                "properties": {
                    "filepath": {"type": "string", "description": "Absolute path to the file to read."}
                },
                "required": ["filepath"],
            },
        }
    },
    {
        "type": "function",
        "function": {
            "name": "write_file",
            "description": "Create a new file or overwrite an existing file with complete content.",
            "parameters": {
                "type": "object",
                "properties": {
                    "filepath": {"type": "string", "description": "Absolute path to the file."},
                    "content": {"type": "string", "description": "The entire content to write into the file."}
                },
                "required": ["filepath", "content"],
            },
        }
    },
    {
        "type": "function",
        "function": {
            "name": "edit_file",
            "description": "Edit an existing file by replacing exact occurrences of a string with a new string.",
            "parameters": {
                "type": "object",
                "properties": {
                    "filepath": {"type": "string", "description": "Absolute path to the file."},
                    "old_string": {"type": "string", "description": "The exact string sequence to be replaced."},
                    "new_string": {"type": "string", "description": "The new string sequence to insert."}
                },
                "required": ["filepath", "old_string", "new_string"],
            },
        }
    }
]

def get_trading_tools():
    """Returns the schemas for official KIS trading execution tools."""
    return [
        {
            "type": "function",
            "function": {
                "name": "get_buyable_cash",
                "description": "Fetch available cash balance in KRW for purchasing stocks.",
                "parameters": { "type": "object", "properties": {} }
            }
        },
        {
            "type": "function",
            "function": {
                "name": "get_portfolio",
                "description": "Fetch current stock holdings and their PnL.",
                "parameters": { "type": "object", "properties": {} }
            }
        },
        {
            "type": "function",
            "function": {
                "name": "place_order",
                "description": "Place a buy or sell order for a Korean or US stock.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "code": {"type": "string", "description": "Stock ticker/code (e.g. 005930 or AAPL)"},
                        "qty": {"type": "integer", "description": "number of shares"},
                        "price": {"type": "number", "description": "limit price (0 for KR market order, $>0 for US limit)"},
                        "is_buy": {"type": "boolean", "description": "True for Buy, False for Sell"},
                        "market": {"type": "string", "description": "'KR' or 'US'"}
                    },
                    "required": ["code", "qty", "price", "is_buy"]
                }
            }
        }
    ]

CORE_TOOLS_SCHEMA.extend(get_trading_tools())

def get_openbb_tools():
    """Returns schemas for OpenBB data tools."""
    return [
        {
            "type": "function",
            "function": {
                "name": "get_us_stock_price",
                "description": "미국 주식의 역사 가격 데이터를 조회합니다. 삼성전자 한국 주식과 NVDA 미국 주식을 비교할 때 유용합니다.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "symbol": {
                            "type": "string",
                            "description": "주식 티커 심볼 (예: AAPL, NVDA, MSFT, TSLA)"
                        },
                        "period": {
                            "type": "string",
                            "description": "기간 (예: 1d, 5d, 1mo, 3mo, 6mo, 1y, 2y, 5y)",
                            "default": "1mo"
                        }
                    },
                    "required": ["symbol"]
                }
            }
        },
        {
            "type": "function",
            "function": {
                "name": "get_fundamental_data",
                "description": "주식의 펀더멘털 데이터 (밸런스시트, 손익계산서)를 조회합니다. 삼성전자와 NVDA의 재무 데이터를 비교할 때 유용합니다.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "symbol": {
                            "type": "string",
                            "description": "주식 티커 심볼 (예: AAPL, NVDA, MSFT, TSLA)"
                        }
                    },
                    "required": ["symbol"]
                }
            }
        },
        {
            "type": "function",
            "function": {
                "name": "get_fundamental_ratios",
                "description": "주식의 주요 펀더멘털 비율 (PER, PBR, ROE 등)을 조회합니다. 삼성전자와 NVDA의 밸류에이션을 비교할 때 유용합니다.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "symbol": {
                            "type": "string",
                            "description": "주식 티커 심볼 (예: AAPL, NVDA, MSFT, TSLA)"
                        }
                    },
                    "required": ["symbol"]
                }
            }
        },
        {
            "type": "function",
            "function": {
                "name": "get_stock_news",
                "description": "주식 관련 최신 뉴스를 조회합니다. MarketCrawler 통합 엔진을 통해 Naver, yfinance, Finnhub 데이터를 합산하여 제공합니다.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "symbol": {
                            "type": "string",
                            "description": "주식 티커 심볼 (예: 005930, AAPL, NVDA)"
                        },
                        "limit": {
                            "type": "integer",
                            "description": "뉴스 개수 (기본값: 10)",
                            "default": 10
                        }
                    },
                    "required": ["symbol"]
                }
            }
        }
    ]

CORE_TOOLS_SCHEMA.extend(get_openbb_tools())

import sys
from mcp_bridge.kis_trading import KISOfficialClient

_kis_client = None
_openbb_server = None

def get_kis_client():
    global _kis_client
    if not _kis_client:
        _kis_client = KISOfficialClient()
    return _kis_client

def get_openbb_server():
    """Get OpenBB server instance"""
    global _openbb_server
    if _openbb_server is None:
        from mcp.openbb_server import OpenBBMCPServer
        _openbb_server = OpenBBMCPServer()
    return _openbb_server

async def dispatch_core_tool(tool_name: str, arguments: dict):
    """Executes the function mapped to the tool_name and returns string result."""
    if tool_name == "execute_bash":
        return execute_bash(**arguments)
    elif tool_name == "read_file":
        return read_file(**arguments)
    elif tool_name == "write_file":
        return write_file(**arguments)
    elif tool_name == "edit_file":
        return edit_file(**arguments)
    elif tool_name == "get_buyable_cash":
        try:
            val = get_kis_client().get_buyable_cash()
            return f"Buyable Cash: {val:,.0f} KRW"
        except Exception as e:
            return f"API Error: {str(e)}"
    elif tool_name == "get_portfolio":
        try:
            pf = get_kis_client().get_portfolio()
            return f"Portfolio: {pf}" if pf else "Portfolio is currently empty."
        except Exception as e:
            return f"API Error: {str(e)}"
    elif tool_name == "get_us_stock_price":
        try:
            result = await get_openbb_server().call_tool(
                "get_us_stock_price",
                arguments
            )
            return result
        except Exception as e:
            return f"OpenBB Error: {str(e)}"
    elif tool_name == "get_fundamental_data":
        try:
            result = await get_openbb_server().call_tool(
                "get_fundamental_data",
                arguments
            )
            return result
        except Exception as e:
            return f"OpenBB Error: {str(e)}"
    elif tool_name == "get_fundamental_ratios":
        try:
            result = await get_openbb_server().call_tool(
                "get_fundamental_ratios",
                arguments
            )
            return result
        except Exception as e:
            return f"OpenBB Error: {str(e)}"
    elif tool_name == "get_stock_news":
        try:
            result = await get_openbb_server().call_tool(
                "get_stock_news",
                arguments
            )
            return result
        except Exception as e:
            return f"OpenBB Error: {str(e)}"
    elif tool_name == "place_order":
        try:
            market_arg = arguments.get("market", "KR")
            action = "BUY" if arguments["is_buy"] else "SELL"
            
            # 1. Market Hours Protection (Restored from TradingAgent)
            from core.market_hours import is_market_open
            if not is_market_open(market_arg):
                msg = f"Market Closed: Cannot execute {action} order for {arguments['code']} because the {market_arg} market is currently closed."
                print(f"[REJECTED] {msg}")
                from core.notification import send_notification
                send_notification(f"⚠️ *주문 거절 (시장 종료)*\n{msg}")
                return msg

            # 2. Execute Order
            success = get_kis_client().place_order(arguments["code"], arguments["qty"], arguments["price"], arguments["is_buy"], market=market_arg)
            
            if success:
                # Log to CSV and JSON State
                from core.monitor import TradeLogger, TradeMonitor
                from core.notification import send_notification
                
                logger = TradeLogger(log_dir="logs")
                monitor = TradeMonitor(state_file="logs/active_trades.json")
                
                # Write to trades_log.csv
                logger.log_trade(
                    stock_code=arguments["code"], 
                    action=action, 
                    quantity=arguments["qty"], 
                    price=arguments["price"], 
                    reasoning=arguments.get("reasoning", "LLM Automated Decision")
                )
                
                # Update active_trades.json
                if action == "BUY":
                    trade_info = {
                        "stock_code": arguments["code"],
                        "purchase_price": arguments["price"],
                        "quantity": arguments["qty"],
                        "status": "active",
                        "market_type": market_arg
                    }
                    monitor.register_trade(arguments["code"], trade_info)
                elif action == "SELL":
                    monitor.remove_trade(arguments["code"])
                    
                # Notify Discord/Slack
                send_notification(f"✅ {action} '{arguments['code']}' {arguments['qty']} shares @ {arguments['price']} ({market_arg})")
                
                return f"Successfully placed {action} order for {arguments['qty']} shares of {arguments['code']}." 
            else:
                msg = f"Failed to place {action} order for {arguments['code']}. Check logs for KIS API error."
                print(f"[FAILED] {msg}")
                return msg
        except Exception as e:
            return f"API Error: {str(e)}"
    else:
        return f"Unknown core tool: {tool_name}"
