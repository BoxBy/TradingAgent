"""
OpenBB MCP Server - 미국 주식, 펀더멘털, 경제 지표 데이터 제공
"""
import logging
from typing import Dict, List, Any, Optional
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)

class OpenBBMCPServer:
    """OpenBB 기반 MCP 서버"""

    def __init__(self):
        self.name = "openbb"
        self.tools = self._get_tools()

    def _get_tools(self) -> List[Dict[str, Any]]:
        """OpenBB 기능을 MCP 툴로 정의"""
        return [
            {
                "name": "get_us_stock_price",
                "description": "미국 주식의 역사 가격 데이터를 조회합니다.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "symbol": {
                            "type": "string",
                            "description": "주식 티커 심볼 (예: AAPL, NVDA, MSFT)"
                        },
                        "period": {
                            "type": "string",
                            "description": "기간 (예: 1d, 5d, 1mo, 3mo, 6mo, 1y, 2y, 5y)",
                            "default": "1mo"
                        }
                    },
                    "required": ["symbol"]
                }
            },
            {
                "name": "get_fundamental_data",
                "description": "주식의 펀더멘털 데이터 (밸런스시트, 손익계산서)를 조회합니다.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "symbol": {
                            "type": "string",
                            "description": "주식 티커 심볼 (예: AAPL, NVDA, MSFT)"
                        }
                    },
                    "required": ["symbol"]
                }
            },
            {
                "name": "get_fundamental_ratios",
                "description": "주식의 주요 펀더멘털 비율 (PER, PBR, ROE 등)을 조회합니다.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "symbol": {
                            "type": "string",
                            "description": "주식 티커 심볼 (예: AAPL, NVDA, MSFT)"
                        }
                    },
                    "required": ["symbol"]
                }
            },
            {
                "name": "get_economic_indicator",
                "description": "경제 지표를 조회합니다 (금리, 인플레이션, GDP 등).",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "indicator": {
                            "type": "string",
                            "description": "경제 지표 (예: fedfunds, cpi, gdp, unemployment)"
                        },
                        "period": {
                            "type": "string",
                            "description": "기간 (예: 1y, 2y, 5y)",
                            "default": "1y"
                        }
                    },
                    "required": ["indicator"]
                }
            },
            {
                "name": "get_stock_news",
                "description": "주식 관련 뉴스를 조회합니다.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "symbol": {
                            "type": "string",
                            "description": "주식 티커 심볼 (예: AAPL, NVDA, MSFT)"
                        },
                        "limit": {
                            "type": "integer",
                            "description": "뉴스 개수",
                            "default": 10
                        }
                    },
                    "required": ["symbol"]
                }
            }
        ]

    async def call_tool(self, tool_name: str, arguments: Dict[str, Any]) -> str:
        """OpenBB 툴을 실행"""
        try:
            if tool_name == "get_us_stock_price":
                return await self._get_us_stock_price(
                    arguments.get("symbol"),
                    arguments.get("period", "1mo")
                )
            elif tool_name == "get_fundamental_data":
                return await self._get_fundamental_data(arguments.get("symbol"))
            elif tool_name == "get_fundamental_ratios":
                return await self._get_fundamental_ratios(arguments.get("symbol"))
            elif tool_name == "get_economic_indicator":
                return await self._get_economic_indicator(
                    arguments.get("indicator"),
                    arguments.get("period", "1y")
                )
            elif tool_name == "get_stock_news":
                return await self._get_stock_news(
                    arguments.get("symbol"),
                    arguments.get("limit", 10)
                )
            else:
                return f"Unknown tool: {tool_name}"
        except Exception as e:
            logger.error(f"OpenBB tool error: {e}")
            return f"Error executing {tool_name}: {str(e)}"

    async def _get_us_stock_price(self, symbol: str, period: str = "1mo") -> str:
        """미국 주식 가격 조회"""
        try:
            from openbb import obb
            from core.ticker_utils import format_ticker_for_yfinance
            symbol_yf = format_ticker_for_yfinance(symbol)

            # 시장 종료 방지 - Yahoo Finance 데이터 사용
            result = obb.equity.price.historical(
                symbol=symbol_yf,
                period=period,
                provider="yfinance"
            )

            # OpenBB 최신 버전: OBBobject에서 DataFrame 추출
            if result is None:
                return f"No data found for {symbol}"

            # OBBobject에서 DataFrame 추출
            df = result.to_df() if hasattr(result, 'to_df') else (result.to_dataframe() if hasattr(result, 'to_dataframe') else result)

            if df is None or (hasattr(df, 'empty') and df.empty) or (hasattr(df, '__len__') and len(df) == 0):
                return f"No data found for {symbol}"

            # 데이터 포맷팅
            latest = df.iloc[-1]
            price = latest.get('close', 0)
            change = latest.get('close', 0) - df.iloc[-2].get('close', 0) if len(df) > 1 else 0
            change_pct = (change / df.iloc[-2].get('close', 1)) * 100 if len(df) > 1 else 0

            return f"""
📊 {symbol} 주가 정보 (기간: {period})
━━━━━━━━━━━━━━━━━━━━
현재 가격: ${price:.2f}
변동: ${change:.2f} ({change_pct:+.2f}%)
데이터 포인트: {len(df)} 개
최근 데이터: {df.index[-1].strftime('%Y-%m-%d')}
            """.strip()

        except ImportError:
            logger.warning("OpenBB not installed properly")
            return "OpenBB 패키지가 올바르게 설치되지 않았습니다."
        except Exception as e:
            logger.error(f"US stock price error: {e}")
            return f"{symbol} 주가 조회 오류: {str(e)}"

    async def _get_fundamental_data(self, symbol: str) -> str:
        """펀더멘털 데이터 조회"""
        try:
            from openbb import obb
            from core.ticker_utils import format_ticker_for_yfinance
            symbol_yf = format_ticker_for_yfinance(symbol)

            # 손익계산서
            income = obb.equity.fundamental.income(symbol=symbol_yf, provider="yfinance")
            # 밸런스시트
            balance = obb.equity.fundamental.balance(symbol=symbol_yf, provider="yfinance")

            # OBBject를 DataFrame으로 변환
            income_df = income.to_df() if hasattr(income, 'to_df') else (income.to_dataframe() if hasattr(income, 'to_dataframe') else income)
            balance_df = balance.to_df() if hasattr(balance, 'to_df') else (balance.to_dataframe() if hasattr(balance, 'to_dataframe') else balance)

            result = f"""
📋 {symbol} 펀더멘털 데이터
━━━━━━━━━━━━━━━━━━━━
            """.strip()

            if income_df is not None and not income_df.empty:
                latest_income = income_df.iloc[-1]
                revenue = latest_income.get('total_revenue', 0)
                net_income = latest_income.get('net_income', 0)
                result += f"\n손익계산서 (최근):\n  매출: ${revenue/1e9:.2f}B\n  순이익: ${net_income/1e9:.2f}B"

            if balance_df is not None and not balance_df.empty:
                latest_balance = balance_df.iloc[-1]
                total_assets = latest_balance.get('total_assets', 0)
                total_liab = latest_balance.get('total_liab', 0)
                result += f"\n밸런스시트 (최근):\n  총자산: ${total_assets/1e9:.2f}B\n  총부채: ${total_liab/1e9:.2f}B"

            return result if result else "펀더멘털 데이터를 찾을 수 없습니다."

        except Exception as e:
            logger.error(f"Fundamental data error: {e}")
            return f"{symbol} 펀더멘털 데이터 조회 오류: {str(e)}"

    async def _get_fundamental_ratios(self, symbol: str) -> str:
        """펀더멘털 비율 조회"""
        try:
            from openbb import obb
            from core.ticker_utils import format_ticker_for_yfinance
            symbol_yf = format_ticker_for_yfinance(symbol)

            # 주요 비율 데이터 조회 (valuation 대신 fundamental.metrics 또는 fundamental.ratios 고려)
            try:
                # OpenBB v4에서는 fundamental.metrics 또는 fundamental.ratios 등을 사용할 수 있음
                # 우선 fundamental.metrics 시도 (가장 일반적)
                result_obj = obb.equity.fundamental.metrics(symbol=symbol_yf, provider="yfinance")
            except Exception:
                try:
                    # 또는 fundamental.ratios 시도
                    result_obj = obb.equity.fundamental.ratios(symbol=symbol_yf, provider="yfinance")
                except Exception:
                    # 백업으로 가격 데이터에서 기본 정보 추출 시도
                    return f"{symbol} 비율 데이터를 조회할 수 없습니다 (지원되지 않는 속성)."

            df = result_obj.to_df() if hasattr(result_obj, 'to_df') else (result_obj.to_dataframe() if hasattr(result_obj, 'to_dataframe') else result_obj)

            if df is None or (hasattr(df, 'empty') and df.empty) or (hasattr(df, '__len__') and len(df) == 0):
                return f"{symbol} 비율 데이터를 찾을 수 없습니다."

            latest = df.iloc[-1]

            result = f"""
📊 {symbol} 주요 비율
━━━━━━━━━━━━━━━━━━━━
            """.strip()

            # 주요 지표 추출
            metrics = {
                'PE Ratio': 'pe',
                'PB Ratio': 'pb',
                'PS Ratio': 'ps',
                'ROE (%)': 'roe',
                'ROA (%)': 'roa',
                'Dividend Yield (%)': 'dividend_yield',
                'Market Cap (B)': 'market_cap'
            }

            for name, key in metrics.items():
                value = latest.get(key)
                if value is not None:
                    if 'Market Cap' in name:
                        result += f"\n{name}: ${value/1e9:.2f}B"
                    elif 'Yield' in name or 'RO' in name:
                        result += f"\n{name}: {value:.2f}%"
                    else:
                        result += f"\n{name}: {value:.2f}"

            return result

        except Exception as e:
            logger.error(f"Fundamental ratios error: {e}")
            return f"{symbol} 비율 조회 오류: {str(e)}"

    async def _get_economic_indicator(self, indicator: str, period: str = "1y") -> str:
        """경제 지표 조회"""
        try:
            from openbb import obb

            result = obb.economy.gdp(indicator=indicator, period=period)

            # OBBject needs to be converted to DataFrame or access its results
            df = result.to_df() if hasattr(result, 'to_df') else (result.to_dataframe() if hasattr(result, 'to_dataframe') else result)

            if df is None or (hasattr(df, 'empty') and df.empty) or (hasattr(df, '__len__') and len(df) == 0):
                return f"경제 지표 '{indicator}' 데이터를 찾을 수 없습니다."

            latest = df.iloc[-1]
            previous = df.iloc[-2] if len(df) > 1 else None

            result_str = f"""
📈 {indicator.upper()} 경제 지표
━━━━━━━━━━━━━━━━━━━━
최근 값: {latest.iloc[0] if len(latest) > 0 else 'N/A'}
            """.strip()

            if previous is not None and len(previous) > 0:
                change = latest.iloc[0] - previous.iloc[0]
                change_pct = (change / previous.iloc[0]) * 100 if previous.iloc[0] != 0 else 0
                result_str += f"\n이전 값: {previous.iloc[0]}\n변동: {change:+.2f} ({change_pct:+.2f}%)"

            return result_str

        except Exception as e:
            logger.error(f"Economic indicator error: {e}")
            return f"경제 지표 조회 오류: {str(e)}"

    async def _get_stock_news(self, symbol: str, limit: int = 10) -> str:
        """주식 뉴스 조회 (MarketCrawler 통합 엔진 사용)"""
        try:
            from data.crawler import MarketCrawler
            crawler = MarketCrawler()
            news = crawler.get_consolidated_stock_news(symbol, limit=limit)

            if not news:
                return f"{symbol} 관련 뉴스를 찾을 수 없습니다."

            header = f"📰 {symbol} 관련 통합 뉴스 (최근 {len(news)}개)"
            separator = "━━━━━━━━━━━━━━━━━━━━"
            result = f"{header}\n{separator}"

            for item in news:
                title = item.get('headline', '제목 없음')
                date_ts = item.get('datetime', 0)
                url = item.get('url', '')
                source = item.get('source', '알 수 없음')

                if date_ts:
                    try:
                        date_str = datetime.fromtimestamp(float(date_ts)).strftime('%Y-%m-%d %H:%M')
                    except Exception:
                        date_str = '날짜 형식 오류'
                else:
                    date_str = '날짜 없음'

                result += f"\n\n📌 {title}"
                result += f"\n   📅 {date_str} | 🏢 {source}"
                result += f"\n   🔗 {url}" if url else ""

            return result

        except Exception as e:
            logger.error(f"Stock news error: {e}")
            return f"{symbol} 뉴스 조회 오류: {str(e)}"


# 단일 인스턴스
_openbb_server = None

def get_openbb_server() -> OpenBBMCPServer:
    """OpenBB MCP 서버 인스턴스 반환"""
    global _openbb_server
    if _openbb_server is None:
        _openbb_server = OpenBBMCPServer()
    return _openbb_server
