# src/utils/ticker_utils.py

import os
import pandas as pd
from .. import config
from ..utils import logger

log = logger.get_logger(__name__)

_TICKER_TO_NAME_MAP = {}

def init_ticker_map():
    """
    봇 시작 시 한 번만 호출되어, 모든 시장의 종목코드-종목명 맵을 하나의 딕셔너리에 로드합니다.
    """
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

                for index, row in df.iterrows():
                    _TICKER_TO_NAME_MAP[row[info["code_col"]]] = row[info["name_col"]]
                
                log.info(f"Successfully loaded and merged {len(df)} tickers from market '{market}'")
            else:
                log.warning(f"Ticker file not found for market {market}: {info['path']}")
        except Exception as e:
            log.error(f"Failed to load ticker map for market {market}. Error: {e}", exc_info=True)

# 종목코드를 보고 시장을 추론하는 헬퍼 함수 추가
def get_market_from_ticker(ticker_code: str) -> str:
    """ 종목코드의 형식을 보고 'KR' 또는 'US'를 반환합니다. """
    if isinstance(ticker_code, float):
        ticker_code = int(ticker_code)
    if isinstance(ticker_code, int):
        return "KR"
    else:
        return "US"

# 시장별 종목 리스트를 실시간으로 필터링하여 반환하도록 수정
def get_tickers_by_market(market_type: str) -> list:
    """ 
    전체 맵을 순회하며 주어진 market_type에 맞는 종목코드 리스트를 실시간으로 생성하여 반환합니다.
    """
    if not _TICKER_TO_NAME_MAP:
        log.warning("Ticker map is not initialized. Cannot get tickers by market.")
        return []
        
    return [
        ticker for ticker in _TICKER_TO_NAME_MAP.keys()
        if get_market_from_ticker(ticker) == market_type
    ]

def get_stock_name(ticker_code: str) -> str:
    """ 종목코드를 받아 종목명을 반환합니다. """
    return _TICKER_TO_NAME_MAP.get(ticker_code, ticker_code)

def format_for_slack(ticker_code: str, width: int = 25) -> str:
    """ 슬랙 표시에 적합한 '종목명[종목코드]' 포맷으로 우측 정렬하여 반환합니다. """
    name = get_stock_name(ticker_code)
    if name == ticker_code:
        return ticker_code.rjust(width)
    formatted_string = f"{name}[{ticker_code}]"
    return formatted_string.rjust(width)