import os
import json
import subprocess
import shutil
import requests
from datetime import datetime
from typing import Dict, Any, List, Optional, Union

# RTK (Rust Token Killer) — CLI proxy that compresses command output to save LLM tokens
_RTK_BIN = shutil.which("rtk")  # None if not installed

def _rtk_wrap_command(command: str) -> str:
    """Rewrites known commands to use rtk for token-optimized output.
    Falls back to original command if rtk is not available or command is not supported."""
    if not _RTK_BIN:
        return command

    # Commands that benefit from RTK filtering
    rtk_prefixes = [
        "git ", "ls ", "ls\t", "tree ", "find ", "grep ", "rg ",
        "cat ", "head ", "tail ", "diff ",
        "docker ", "kubectl ",
        "pytest", "cargo test", "go test", "jest", "vitest",
        "ruff check", "eslint", "tsc",
        "gh ", "aws ",
    ]

    stripped = command.strip()
    for prefix in rtk_prefixes:
        if stripped.startswith(prefix):
            return f"{_RTK_BIN} {stripped}"

    return command

def execute_bash(command: str) -> str:
    """Executes a bash command and returns the output explicitly.
    Uses RTK (Rust Token Killer) to compress output when available."""
    # Safety Check for destructive commands could go here
    try:
        wrapped = _rtk_wrap_command(command)
        result = subprocess.run(
            wrapped, shell=True, check=True, capture_output=True, text=True, timeout=120
        )
        return result.stdout
    except subprocess.CalledProcessError as e:
        # On RTK failure, retry with original command
        if wrapped != command:
            try:
                result = subprocess.run(
                    command, shell=True, check=True, capture_output=True, text=True, timeout=120
                )
                return result.stdout
            except subprocess.CalledProcessError as e2:
                return f"Error executing command:\nSTDOUT:\n{e2.stdout}\nSTDERR:\n{e2.stderr}"
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

def update_trading_strategy(updates: Dict[str, Any]) -> str:
    """Updates the dynamic trading strategy in strategy.json with safety checks."""
    import json
    strategy_path = "/home/ubuntu/TradingAgent/data/strategy.json"
    try:
        if os.path.exists(strategy_path):
            with open(strategy_path, "r") as f:
                strategy = json.load(f)
        else:
            strategy = {}

        # Safety Bounds
        MAX_TP = 3.5
        MAX_SL = 10.0
        MIN_CASH = 0.10

        if "target_profit_pct" in updates:
            updates["target_profit_pct"] = min(updates["target_profit_pct"], MAX_TP)
        if "stop_loss_pct" in updates:
            updates["stop_loss_pct"] = min(updates["stop_loss_pct"], MAX_SL)
        if "min_cash_reserve_ratio" in updates:
            updates["min_cash_reserve_ratio"] = max(updates["min_cash_reserve_ratio"], MIN_CASH)

        strategy.update(updates)
        
        with open(strategy_path, "w") as f:
            json.dump(strategy, f, indent=4)
        
        return f"Strategy updated successfully: {json.dumps(updates)}"
    except Exception as e:
        return f"Error updating strategy: {str(e)}"

def search_web(query: str, search_depth: str = "advanced") -> str:
    """Performs a web search using the Tavily API for macro-economic analysis and news."""
    api_key = os.getenv("TAVILY_API_KEY")
    if not api_key:
        return "Error: TAVILY_API_KEY not found in environment."
    
    url = "https://api.tavily.com/search"
    headers = {
        "Content-Type": "application/json",
    }
    # Tavily API allows API key in the JSON body as 'api_key' or in headers if using their SDK.
    # The user provided a curl example with Authorization: Bearer.
    headers["Authorization"] = f"Bearer {api_key}"
    
    data = {
        "query": query,
        "search_depth": search_depth,
        "include_answer": True,
        "max_results": 5
    }
    
    try:
        response = requests.post(url, headers=headers, json=data, timeout=30)
        response.raise_for_status()
        results = response.json()
        
        # Format the results for the LLM
        output = f"Tavily Search Results for '{query}':\n"
        if "answer" in results and results["answer"]:
            output += f"Summary Answer: {results['answer']}\n\n"
        
        raw_results = results.get("results", [])
        if not raw_results:
            return f"No results found for '{query}'."

        for idx, res in enumerate(raw_results, 1):
            output += f"{idx}. {res.get('title')} ({res.get('url')})\n"
            output += f"   {res.get('content', '')[:500]}...\n\n"
            
        return output
    except Exception as e:
        return f"Error performing Tavily search: {str(e)}"

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
    },
    {
        "type": "function",
        "function": {
            "name": "update_trading_strategy",
            "description": "Adjust dynamic trading parameters (TP, SL, Cash Reserve). Requires reasoning.",
            "parameters": {
                "type": "object",
                "properties": {
                    "updates": {
                        "type": "object",
                        "description": "Dictionary of parameters to update: target_profit_pct, stop_loss_pct, min_cash_reserve_ratio, etc."
                    },
                    "reasoning": {
                        "type": "string",
                        "description": "Justification for changing the strategy based on market analysis."
                    }
                },
                "required": ["updates", "reasoning"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "search_web",
            "description": "Perform an advanced web search for real-time news, macro-economic data, and market trends.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "The search query (e.g., 'FED interest rate decision today', 'NVDA earnings expectations')"},
                    "search_depth": {"type": "string", "description": "Search depth: 'basic' or 'advanced'", "default": "advanced"}
                },
                "required": ["query"]
            }
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
                        "price": {"type": "number", "description": "limit price. REQUIRED for US stocks - must be >0 (use current market price). Set 0 for KR market order."},
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
from core.monitor import get_system_logger
from core.ticker_utils import get_stock_name

# 주문 실행 로깅용 main logger (main.py에서 초기화된 것과 동일 인스턴스)
_main_logger = get_system_logger('main')

_kis_client = None
_openbb_server = None

def get_kis_client():
    global _kis_client
    if not _kis_client:
        _kis_client = KISOfficialClient()
    return _kis_client

def _fetch_us_price_finnhub(symbol: str) -> float:
    """Fetch current US stock price via Finnhub API. Returns 0 on failure."""
    fh_key = os.getenv('FINNHUB_API_KEY', '')
    if not fh_key:
        return 0.0
    try:
        import requests as _req
        url = f'https://finnhub.io/api/v1/quote?symbol={symbol}&token={fh_key}'
        res = _req.get(url, timeout=5)
        if res.status_code == 200:
            data = res.json()
            price = float(data.get('c', 0))
            if price > 0:
                return price
    except Exception:
        pass
    return 0.0

def get_openbb_server():
    """Get OpenBB server instance — DEPRECATED, use _fetch_us_price_finnhub instead.
    Kept for backward compat but will raise ImportError (mcp/ dir was removed)."""
    global _openbb_server
    if _openbb_server is None:
        from mcp.openbb_server import OpenBBMCPServer  # noqa: will fail intentionally
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
        symbol = arguments.get("symbol") or arguments.get("code", "")
        try:
            price = _fetch_us_price_finnhub(symbol)
            if price > 0:
                return f"현재 가격: ${price:.2f} (ticker: {symbol}, source: Finnhub)"
            else:
                return f"Failed to fetch price for {symbol} (Finnhub returned 0 or no API key)"
        except Exception as e:
            return f"Price fetch error: {str(e)}"
    elif tool_name == "get_fundamental_data":
        symbol = arguments.get("symbol") or arguments.get("code", "")
        try:
            fh_key = os.getenv('FINNHUB_API_KEY', '')
            import requests as _req
            # Profile2 for company info
            r1 = _req.get(f'https://finnhub.io/api/v1/stock/profile2?symbol={symbol}&token={fh_key}', timeout=10)
            d1 = r1.json() if r1.status_code == 200 else {}
            # Metric for financial metrics
            r2 = _req.get(f'https://finnhub.io/api/v1/stock/metric?symbol={symbol}&metric=all&token={fh_key}', timeout=10)
            m = r2.json().get('metric', {}) if r2.status_code == 200 else {}

            mcap = m.get('marketCapitalization', d1.get('marketCapitalization', 0))
            mcap_str = f"${mcap/1e6:.2f}T" if mcap and mcap > 1e6 else f"${mcap:.0f}M" if mcap else "N/A"
            revenue = m.get('revenueTTM', 0)
            ni = m.get('netIncomeTTM', 0)
            result = (f"📋 {symbol} 펀더멘털 데이터\n"
                      f"━━━━━━━━━━━━━━━━━━━━\n"
                      f"회사명: {d1.get('name', 'N/A')}\n"
                      f"산업: {d1.get('finnhubIndustry', 'N/A')}\n"
                      f"시가총액: {mcap_str}\n"
                      f"매출(TTM): ${revenue/1e9:.2f}B\n" if revenue else "매출: N/A\n")
            result += f"순이익(TTM): ${ni/1e9:.2f}B" if ni else "순이익: N/A"
            return result
        except Exception as e:
            return f"{symbol} 펀더멘털 데이터 조회 오류: {str(e)}"
    elif tool_name == "get_fundamental_ratios":
        symbol = arguments.get("symbol") or arguments.get("code", "")
        try:
            fh_key = os.getenv('FINNHUB_API_KEY', '')
            import requests as _req
            r = _req.get(f'https://finnhub.io/api/v1/stock/metric?symbol={symbol}&metric=all&token={fh_key}', timeout=10)
            m = r.json().get('metric', {}) if r.status_code == 200 else {}
            result = f"📊 {symbol} 주요 비율\n━━━━━━━━━━━━━━━━━━━━"
            for name, key in [('PE Ratio', 'peNormalizedAnnual'), ('PB Ratio', 'pbAnnual'),
                              ('ROE (%)', 'roeTTM'), ('ROA (%)', 'roaTTM'),
                              ('Revenue Growth YoY (%)', 'revenueGrowthTTMYoy'),
                              ('EPS Growth 5Y (%)', 'epsGrowth5Y'),
                              ('Dividend Yield (%)', 'dividendYieldIndicatedAnnual'),
                              ('Beta', 'beta')]:
                v = m.get(key)
                if v is not None:
                    result += f"\n{name}: {v:.2f}"
            return result
        except Exception as e:
            return f"{symbol} 비율 조회 오류: {str(e)}"
    elif tool_name == "get_stock_news":
        symbol = arguments.get("symbol") or arguments.get("code", "")
        limit = arguments.get("limit", 10)
        try:
            fh_key = os.getenv('FINNHUB_API_KEY', '')
            import requests as _req
            from datetime import datetime, timedelta
            today = datetime.now().strftime('%Y-%m-%d')
            week_ago = (datetime.now() - timedelta(days=14)).strftime('%Y-%m-%d')
            r = _req.get(f'https://finnhub.io/api/v1/company-news?symbol={symbol}&from={week_ago}&to={today}&token={fh_key}', timeout=10)
            articles = r.json() if r.status_code == 200 else []
            articles = articles[:limit]
            if not articles:
                return f"{symbol} 관련 뉴스를 찾을 수 없습니다."
            result = f"📰 {symbol} 관련 뉴스 (최근 {len(articles)}개)\n━━━━━━━━━━━━━━━━━━━━"
            for a in articles:
                headline = a.get('headline', '')
                source = a.get('source', '')
                ts = a.get('datetime', 0)
                date_str = datetime.fromtimestamp(ts).strftime('%Y-%m-%d %H:%M') if ts else 'N/A'
                url = a.get('url', '')
                result += f"\n\n📌 {headline}\n   📅 {date_str} | 🏢 {source}\n   🔗 {url}" if url else f"\n\n📌 {headline}\n   📅 {date_str} | 🏢 {source}"
            return result
        except Exception as e:
            return f"{symbol} 뉴스 조회 오류: {str(e)}"
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
                send_notification(f"⚠️ *주문 거절 (시장 종료)*\n{msg}", source="RiskExecuter", category="order_rejected")
                return msg

            # 1.5. KR Cash Guard — prevent buying when KR cash is negative
            if action == "BUY" and market_arg.upper() == "KR":
                try:
                    _kr_cash = get_kis_client().get_buyable_cash()
                    if _kr_cash < 0:
                        msg = (f"❌ KR 매수 불가: 예수금 마이너스 (₩{_kr_cash:,.0f}). "
                               f"KR 포지션 매도 후 재시도하세요.")
                        print(f"[REJECTED] {msg}")
                        from core.notification import send_notification
                        send_notification(f"🚫 *KR 현금 부족*\n{msg}", source="RiskExecuter", category="order_rejected")
                        return msg
                except Exception as _ce:
                    print(f"[WARN] KR cash check failed (allowing order): {_ce}")

            # 1.5.1. Sector Concentration Check (BUY only)
            if action == "BUY":
                try:
                    _SECTOR_MAP = {
                        # Semiconductor
                        "NVDA": "Semiconductor", "AMD": "Semiconductor", "AVGO": "Semiconductor",
                        "TSM": "Semiconductor", "MRVL": "Semiconductor", "INTC": "Semiconductor",
                        "QCOM": "Semiconductor", "TXN": "Semiconductor", "MU": "Semiconductor",
                        "000660": "Semiconductor", "042700": "Semiconductor", "005930": "Semiconductor",
                        # AI / Software
                        "PLTR": "AI_Software", "MSFT": "AI_Software", "GOOGL": "AI_Software",
                        "META": "AI_Software", "CRM": "AI_Software", "SNOW": "AI_Software",
                        # Cloud / Infrastructure
                        "AMZN": "Cloud", "AMZN": "Cloud", "ORCL": "Cloud",
                    }
                    _MAX_SAME_SECTOR = 4  # Max positions in same sector

                    target_sector = _SECTOR_MAP.get(arguments["code"])
                    if target_sector:
                        # Load current portfolio
                        import json as _json
                        _pf_path = "logs/cached_balance.json"
                        _current_sector_count = 0
                        if os.path.exists(_pf_path):
                            try:
                                with open(_pf_path, "r") as _f:
                                    _cb = _json.load(_f)
                                for _h in _cb.get("last_get_portfolio", []):
                                    if _SECTOR_MAP.get(_h.get("stock_code")) == target_sector:
                                        _current_sector_count += 1
                            except: pass

                        if _current_sector_count >= _MAX_SAME_SECTOR:
                            msg = (f"⚠️ Sector Concentration Limit: {target_sector} 섹터에 이미 {_current_sector_count}개 포지션이 있습니다. "
                                   f"(최대 {_MAX_SAME_SECTOR}개). {arguments['code']} 구매가 거절되었습니다. "
                                   f"다른 섹터의 종목을 고려하세요.")
                            print(f"[REJECTED] {msg}")
                            from core.notification import send_notification
                            send_notification(f"🚫 *섹터 집중도 제한*\n{msg}", source="RiskExecuter", category="order_rejected")
                            return msg
                except Exception as _e:
                    print(f"[WARN] Sector check failed (allowing order): {_e}")

            # 1.6. Auto-fetch current price if price is 0 or missing
            order_price = arguments.get("price", 0)
            if order_price is None or order_price <= 0:
                if market_arg.upper() == "US":
                    try:
                        print(f"[Auto-Price] Fetching current price for US stock {arguments['code']}...")
                        # Use Finnhub directly (yfinance broken on this server)
                        order_price = _fetch_us_price_finnhub(arguments["code"])
                        if order_price > 0:
                            if action == "BUY":
                                # Add 0.5% buffer above market to ensure fill in KIS mock
                                order_price = round(order_price * 1.005, 2)
                            arguments["price"] = order_price
                            print(f"[Auto-Price] ✅ Resolved {arguments['code']} price: ${order_price} (Finnhub, +0.5% buffer for BUY)")
                        else:
                            msg = (f"❌ Cannot place US order for {arguments['code']}: price not specified and Finnhub lookup failed. "
                                   f"US stocks require a specific limit price > 0. Please call get_us_stock_price first to get the current price, "
                                   f"then retry place_order with that price.")
                            print(f"[REJECTED] {msg}")
                            return msg
                    except Exception as _pe:
                        msg = (f"❌ Cannot place US order for {arguments['code']}: price not specified and auto-price lookup error: {_pe}. "
                               f"US stocks require a specific limit price > 0. Call get_us_stock_price first.")
                        print(f"[REJECTED] {msg}")
                        return msg
                elif market_arg.upper() == "KR":
                    # KR stocks: fetch price from cached_balance.json for both BUY and SELL
                    # KIS mock trading rejects price=0, so we must provide a real price
                    def _kr_tick_size(p):
                        """Return the KRX tick size (호가단위) for a given price."""
                        p = abs(int(p))
                        if p < 2000: return 1
                        if p < 5000: return 5
                        if p < 20000: return 10
                        if p < 50000: return 25
                        if p < 100000: return 50
                        if p < 150000: return 100
                        if p < 200000: return 500
                        if p < 500000: return 1000
                        return 5000
                    def _kr_round_to_tick(p, round_down=True):
                        """Round price to nearest valid KRX tick."""
                        tick = _kr_tick_size(p)
                        p = int(p)
                        return (p // tick) * tick if round_down else ((p + tick - 1) // tick) * tick
                    try:
                        _pf_path = "logs/cached_balance.json"
                        if os.path.exists(_pf_path):
                            with open(_pf_path, "r") as _f:
                                _cb = json.load(_f)
                            for _h in _cb.get("last_get_portfolio", []):
                                if _h.get("stock_code") == arguments["code"]:
                                    _cur = _h.get("current_price", 0)
                                    if _cur > 0:
                                        if action == "SELL":
                                            # Undercut by 0.5% for faster fill, then round down to valid tick
                                            _raw_price = int(_cur * 0.995)
                                            arguments["price"] = _kr_round_to_tick(_raw_price, round_down=True)
                                            print(f"[Auto-Price] ✅ Resolved KR SELL {arguments['code']} price from cache: ₩{_cur} → ₩{arguments['price']} (0.5% undercut, tick-rounded)")
                                        else:
                                            arguments["price"] = _kr_round_to_tick(_cur, round_down=False)
                                            print(f"[Auto-Price] ✅ Resolved KR BUY {arguments['code']} price from cache: ₩{_cur} → ₩{arguments['price']} (tick-rounded)")
                                        break
                        if not arguments.get("price") or arguments["price"] <= 0:
                            # Fallback: fetch live price via KIS API (fixes 19-session NaN bug)
                            try:
                                _live_price = get_kis_client().get_kr_current_price(arguments["code"])
                                if _live_price and _live_price > 0:
                                    arguments["price"] = _kr_round_to_tick(_live_price, round_down=False)
                                    print(f"[Auto-Price] ✅ KR {action} {arguments['code']}: live price ₩{_live_price} → ₩{arguments['price']} (tick-rounded)")
                            except Exception as _lp_err:
                                print(f"[Auto-Price] ⚠️ Live price fetch failed for {arguments['code']}: {_lp_err}")

                        if not arguments.get("price") or arguments["price"] <= 0:
                            if action == "SELL":
                                # Last resort: price=0 as market order
                                arguments["price"] = 0
                                print(f"[Auto-Price] ⚠️ KR SELL {arguments['code']}: no cached/live price, falling back to market order (price=0)")
                            else:
                                msg = (f"❌ Cannot place KR BUY order for {arguments['code']}: price not specified and live lookup failed. "
                                       f"Provide a specific price > 0 for KR BUY orders.")
                                print(f"[REJECTED] {msg}")
                                return msg
                    except Exception as _krpe:
                        msg = (f"❌ Cannot place KR order for {arguments['code']}: price lookup failed ({_krpe}). "
                               f"Provide a specific price > 0.")
                        print(f"[REJECTED] {msg}")
                        return msg

            # 2. Execute Order
            success = get_kis_client().place_order(arguments["code"], arguments["qty"], arguments["price"], arguments["is_buy"], market=market_arg)
            
            if success:
                from core.monitor import TradeLogger, TradeMonitor
                from core.notification import send_notification
                
                logger = TradeLogger(log_dir="logs")
                monitor = TradeMonitor(state_file="logs/active_trades.json")
                
                # === PRE-LOG FILL VERIFICATION ===
                # Root cause fix (2026-05-21): KIS API returns rt_cd=0 for ORDER ACCEPTED,
                # not ORDER FILLED. Previously we logged the trade immediately, then detected
                # phantom fills post-hoc. Now we verify the fill BEFORE logging.
                fill_confirmed = False
                phantom_fill = False
                try:
                    import asyncio
                    _kis = get_kis_client()
                    _portfolio = _kis.get_portfolio()
                    if isinstance(_portfolio, list):
                        target_code = arguments["code"].upper()
                        
                        if action == "SELL":
                            # SELL confirmed = position no longer in portfolio (or qty decreased)
                            still_held = any(
                                p.get("stock_code", "").upper() == target_code and p.get("quantity", 0) > 0
                                for p in _portfolio
                            )
                            # If still held with same qty, SELL was NOT filled
                            # But KIS might show stale data briefly — also check if qty decreased
                            if not still_held:
                                fill_confirmed = True
                            else:
                                # Check if quantity decreased (partial fill or full fill of part)
                                for p in _portfolio:
                                    if p.get("stock_code", "").upper() == target_code:
                                        held_qty = p.get("quantity", 0)
                                        if held_qty < arguments["qty"]:
                                            fill_confirmed = True  # At least partial fill
                                        else:
                                            phantom_fill = True
                                        break
                                else:
                                    # Position not found = full fill
                                    fill_confirmed = True
                        
                        elif action == "BUY":
                            # BUY confirmed = position appears in portfolio (or qty increased)
                            in_portfolio = any(
                                p.get("stock_code", "").upper() == target_code and p.get("quantity", 0) > 0
                                for p in _portfolio
                            )
                            if in_portfolio:
                                fill_confirmed = True
                            else:
                                # For US stocks, settlement is T+1 so position might not appear immediately
                                # Give benefit of doubt for US BUY orders
                                if market_arg.upper() == "US":
                                    fill_confirmed = True  # US positions settle T+1
                                    print(f"[Fill Verify] US BUY {target_code}: assuming filled (T+1 settlement)")
                                else:
                                    phantom_fill = True
                        
                        monitor.sync_with_broker_portfolio(_portfolio)
                        
                        # Refresh buyable cash
                        try:
                            _cash = _kis.get_buyable_cash()
                            _cache_path = "logs/cached_balance.json"
                            _cached = {}
                            if os.path.exists(_cache_path):
                                with open(_cache_path, "r") as _cf:
                                    _cached = json.load(_cf)
                            _cached["last_get_buyable_cash"] = _cash
                            with open(_cache_path, "w") as _cf:
                                json.dump(_cached, _cf, default=str)
                        except Exception:
                            pass
                except Exception as _sync_err:
                    print(f"[Fill Verify] Warning: verification failed, assuming filled: {_sync_err}")
                    fill_confirmed = True  # If verification fails, assume filled (fail-open)
                
                # === LOG TRADE ONLY IF FILL CONFIRMED ===
                if fill_confirmed and not phantom_fill:
                    # Write to trades_log.csv
                    logger.log_trade(
                        stock_code=arguments["code"], 
                        action=action, 
                        quantity=arguments["qty"], 
                        price=arguments["price"], 
                        reasoning=arguments.get("reasoning", "LLM Automated Decision")
                    )
                    
                    # ✅ main logger로 주문 실행 기록 (trading_agent.log)
                    _stock_name = get_stock_name(arguments["code"])
                    _price_str = f"₩{arguments['price']:,.0f}" if market_arg.upper() == "KR" else f"${arguments['price']:,.2f}"
                    _main_logger.info(f"[TradingClaw] 📝 Trade executed: {action} {arguments['code']} {_stock_name} x{arguments['qty']} @ {_price_str} ({market_arg}) (fill confirmed)")
                    
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
                    
                    send_notification(f"✅ {action} '{arguments['code']}' {arguments['qty']} shares @ {arguments['price']} ({market_arg})", source="RiskExecuter", category="trade")
                    return f"Successfully placed {action} order for {arguments['qty']} shares of {arguments['code']} at {arguments['price']}."
                
                elif phantom_fill:
                    # Order accepted but NOT filled — do NOT log as trade
                    print(f"[PHANTOM BLOCKED] {action} {arguments['code']} was accepted by API but NOT filled. Not logging as trade.")
                    
                    # 👻 main logger로 phantom fill 기록
                    _stock_name = get_stock_name(arguments["code"])
                    _price_str = f"₩{arguments['price']:,.0f}" if market_arg.upper() == "KR" else f"${arguments['price']:,.2f}"
                    _main_logger.warning(f"[TradingClaw] 👻 Phantom fill detected: {action} {arguments['code']} {_stock_name} x{arguments['qty']} @ {_price_str} ({market_arg}) — NOT logging trade")
                    
                    # Write phantom flag for position filtering
                    try:
                        _flag_path = os.path.join("logs", f"phantom_{arguments['code'].upper()}.flag")
                        with open(_flag_path, "w") as _ff:
                            json.dump({
                                "timestamp": datetime.now().isoformat(),
                                "code": arguments["code"].upper(),
                                "market": market_arg,
                                "qty": arguments["qty"],
                                "action": action,
                                "error": "Order accepted but not filled (pre-log verification)"
                            }, _ff)
                    except Exception:
                        pass
                    
                    if action == "SELL":
                        send_notification(
                            f"🚫 PHANTOM BLOCKED: SELL '{arguments['code']}' {arguments['qty']} shares @ {arguments['price']} ({market_arg}) was accepted but NOT executed. Trade NOT logged.",
                            source="RiskExecuter", category="trade_error"
                        )
                        return f"⚠️ PHANTOM FILL DETECTED: SELL order for {arguments['qty']} shares of {arguments['code']} was accepted by the API but the position still exists in the broker portfolio. The trade was NOT logged. The position is still held."
                    else:
                        send_notification(
                            f"🚫 PHANTOM BLOCKED: BUY '{arguments['code']}' {arguments['qty']} shares @ {arguments['price']} ({market_arg}) was accepted but NOT executed. Trade NOT logged.",
                            source="RiskExecuter", category="trade_error"
                        )
                        return f"⚠️ PHANTOM FILL DETECTED: BUY order for {arguments['qty']} shares of {arguments['code']} was accepted by the API but the stock does NOT appear in the broker portfolio. The trade was NOT logged. No position was created."
            else:
                msg = (f"FAILED: KIS API rejected {action} order for {arguments['code']} ({arguments['qty']} shares @ {arguments['price']}). "
                       f"Do NOT retry the same order. Move to the next stock or try a different price/quantity.")
                print(f"[FAILED] {msg}")
                return msg
        except Exception as e:
            return f"API Error: {str(e)}"
    elif tool_name == "update_trading_strategy":
        return update_trading_strategy(arguments.get("updates", {}))
    elif tool_name == "search_web":
        return search_web(
            query=arguments.get("query"),
            search_depth=arguments.get("search_depth", "advanced")
        )
    else:
        return f"Unknown core tool: {tool_name}"
