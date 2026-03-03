import os
import pandas as pd
import config
from core.monitor import get_system_logger

log = get_system_logger(__name__)

_TICKER_TO_NAME_MAP = {}

def init_ticker_map():
    """봇 시작 시 한 번만 호출되어, 모든 시장의 종목코드-종목명 맵을 로드합니다."""
    log.info("Initializing unified ticker-to-name map...")
    
    market_files = {
        "KR": {"path": os.path.join(config.DATA_DIR, "kospi_tickers.csv"), "code_col": "종목코드", "name_col": "종목명"},
        "US": {"path": os.path.join(config.DATA_DIR, "nasdaq_tickers.csv"), "code_col": "Symbol", "name_col": "Security Name"}
    }

    for market, info in market_files.items():
        try:
            if os.path.exists(info["path"]):
                df = pd.read_csv(info["path"]).fillna(0)
                if market == "KR":
                    df[info["code_col"]] = df[info["code_col"]].astype(str).str.zfill(6)
                for _, row in df.iterrows():
                    _TICKER_TO_NAME_MAP[row[info["code_col"]]] = row[info["name_col"]]
                log.info(f"Loaded {len(df)} tickers from market '{market}'")
            else:
                log.warning(f"Ticker file not found for market {market}: {info['path']}")
        except Exception as e:
            log.error(f"Failed to load ticker map for market {market}: {e}", exc_info=True)

def get_market_from_ticker(ticker_code: str) -> str:
    """종목코드의 형식을 보고 'KR' 또는 'US'를 반환합니다."""
    if isinstance(ticker_code, float):
        ticker_code = str(int(ticker_code))
    
    # 6자리 숫자로만 이루어져 있으면 한국 주식
    if str(ticker_code).isdigit() and len(str(ticker_code)) == 6:
        return "KR"
    
    # .KS나 .KQ가 붙어있어도 한국 주식
    if ".KS" in str(ticker_code) or ".KQ" in str(ticker_code):
        return "KR"
        
    return "US"

def format_ticker_for_yfinance(ticker_code: str) -> str:
    """Yahoo Finance에 적합한 티커 형식으로 변환 (.KS 추가 등)"""
    market = get_market_from_ticker(ticker_code)
    if market == "KR":
        clean_code = str(ticker_code).split('.')[0].zfill(6)
        return f"{clean_code}.KS" # 기본적으로 KOSPI로 가정 (.KQ 구분 필요시 로직 확장 가능)
    return str(ticker_code)

def get_tickers_by_market(market_type: str) -> list:
    if not _TICKER_TO_NAME_MAP:
        log.warning("Ticker map is not initialized.")
        return []
    return [t for t in _TICKER_TO_NAME_MAP.keys() if get_market_from_ticker(t) == market_type]

def get_stock_name(ticker_code: str) -> str:
    """종목코드를 받아 종목명을 반환합니다."""
    return _TICKER_TO_NAME_MAP.get(ticker_code, ticker_code)

def format_for_slack(ticker_code: str, width: int = 25) -> str:
    """슬랙 표시에 적합한 '종목명[종목코드]' 포맷."""
    name = get_stock_name(ticker_code)
    if name == ticker_code:
        return ticker_code
    return f"{name}[{ticker_code}]"
