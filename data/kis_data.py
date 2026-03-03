import time
from pykis import Api, DomainInfo
from config import get_api_key, MOCK_TRADING
from typing import Dict, Any

class KISDataFetcher:
    """
    Standalone KIS API fetcher for read-only market data (Fundamentals, Technicals).
    Replaces the data-fetching portion of TradingAgent's kis_wrapper.py.
    """
    def __init__(self):
        self.app_key = get_api_key("KIS_MOCK_APP_KEY") if MOCK_TRADING else get_api_key("KIS_APP_KEY")
        self.app_secret = get_api_key("KIS_MOCK_APP_SECRET") if MOCK_TRADING else get_api_key("KIS_APP_SECRET")
        self.account_no = get_api_key("KIS_MOCK_ACCOUNT_NO") if MOCK_TRADING else get_api_key("KIS_ACCOUNT_NO")
        
        try:
            domain = DomainInfo.VIRTUAL if MOCK_TRADING else DomainInfo.REAL
            self.api = Api(keyinfo={"appkey": self.app_key, "appsecret": self.app_secret}, domaininfo=domain)
            print("[KISDataFetcher] Successfully initialized pykis (Read-Only).")
        except Exception as e:
            print(f"[KISDataFetcher] Failed to initialize pykis: {e}")
            self.api = None

    def get_stock_basic_info(self, stock_code: str) -> dict:
        """Fetch basic stock info (FHPST01010000)."""
        if not self.api: return {}
        try:
            # using the raw api request mapping as done in TradingAgent
            res = self.api.request('FHPST01010000', {'FID_COND_MRKT_DIV_CODE': 'J', 'FID_INPUT_ISCD': stock_code})
            return res.dict() if res and not res.is_error else {}
        except Exception as e:
            print(f"Error fetching basic info for {stock_code}: {e}")
            return {}

    def get_financial_ratios(self, stock_code: str) -> list:
        """Fetch financial ratios (FHKST66430200)."""
        if not self.api: return []
        try:
            res = self.api.request('FHKST66430200', {'FID_COND_MRKT_DIV_CODE': 'J', 'FID_INPUT_ISCD': stock_code, 'FID_DIV_CLS_CODE': '0'})
            if res and not res.is_error:
                return res.dict().get('output', [])
            return []
        except Exception:
            return []

    def get_balance_sheet(self, stock_code: str) -> list:
        """Fetch balance sheet (FHKST66430100)."""
        if not self.api: return []
        try:
            res = self.api.request('FHKST66430100', {'FID_COND_MRKT_DIV_CODE': 'J', 'FID_INPUT_ISCD': stock_code, 'FID_DIV_CLS_CODE': '0'})
            if res and not res.is_error:
                return res.dict().get('output', [])
            return []
        except Exception:
            return []

    def get_income_statement(self, stock_code: str) -> list:
        """Fetch income statement (FHKST66430300)."""
        if not self.api: return []
        try:
            res = self.api.request('FHKST66430300', {'FID_COND_MRKT_DIV_CODE': 'J', 'FID_INPUT_ISCD': stock_code, 'FID_DIV_CLS_CODE': '0'})
            if res and not res.is_error:
                return res.dict().get('output', [])
            return []
        except Exception:
            return []
            
    def fetch_historical_data(self, stock_code: str, period: int = 200) -> None:
        """Mock placeholder for historical dataloader."""
        pass
