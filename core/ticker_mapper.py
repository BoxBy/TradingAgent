import os
import pandas as pd
from typing import Dict, List, Optional
from core.monitor import get_system_logger

log = get_system_logger(__name__)

class KoreanTickerMapper:
    """한국 종목 회사명-종목코드 매핑 유틸리티"""
    
    def __init__(self, data_dir="data"):
        self.name_to_code: Dict[str, str] = {}
        self.code_to_name: Dict[str, str] = {}
        self.data_dir = data_dir
        self._load_mapping()
    
    def _load_mapping(self):
        csv_path = os.path.join(self.data_dir, "kospi_tickers.csv")
        
        try:
            if not os.path.exists(csv_path):
                log.warning(f"Ticker mapping file {csv_path} not found.")
                return
                
            df = pd.read_csv(csv_path)
            for _, row in df.iterrows():
                code = str(row["종목코드"]).zfill(6)
                name = str(row["종목명"]).strip()
                
                self.name_to_code[name] = code
                self.code_to_name[code] = name
            
            log.info(f"Loaded {len(self.name_to_code)} Korean ticker mappings")
        except Exception as e:
            log.error(f"Failed to load Korean ticker mapping: {e}", exc_info=True)
    
    def get_code(self, company_name: str) -> Optional[str]:
        return self.name_to_code.get(company_name.strip())
    
    def get_name(self, ticker_code: str) -> Optional[str]:
        code = str(ticker_code).zfill(6)
        return self.code_to_name.get(code)
    
    def extract_tickers_from_headlines(self, headlines: List[str]) -> List[str]:
        found_tickers = set()
        for headline in headlines:
            for company_name, ticker_code in self.name_to_code.items():
                if company_name in headline:
                    found_tickers.add(ticker_code)
        
        return sorted(list(found_tickers))

_korean_ticker_mapper = None

def get_korean_ticker_mapper() -> KoreanTickerMapper:
    global _korean_ticker_mapper
    if _korean_ticker_mapper is None:
        _korean_ticker_mapper = KoreanTickerMapper()
        # Fallback to copy from TrainingAgent if missing
        if not os.path.exists(os.path.join("data", "kospi_tickers.csv")):
            try:
                os.makedirs("data", exist_ok=True)
                import shutil
                shutil.copy("/home/ubuntu/TradingAgent/data/kospi_tickers.csv", "data/kospi_tickers.csv")
                _korean_ticker_mapper._load_mapping()
            except:
                pass
    return _korean_ticker_mapper
