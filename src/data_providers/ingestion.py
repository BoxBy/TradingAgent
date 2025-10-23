import time
from datetime import datetime, timedelta

import finnhub
import pandas as pd
import requests
import yfinance as yf

from .. import config
from ..utils import logger
from ..utils.api_utils import api_retry_decorator
from .kis_wrapper import TradingInterface

log = logger.get_logger(__name__)


class DataIngestion:
    """
    KIS, Finnhub, yfinance 등 다양한 소스에서 시장 데이터를 가져오는 클래스.
    """

    def __init__(self, kis_wrapper: TradingInterface):
        self.kis_wrapper = kis_wrapper
        if config.FINNHUB_API_KEY:
            self.finnhub_client = finnhub.Client(api_key=config.FINNHUB_API_KEY)
        else:
            self.finnhub_client = None
            log.warning(
                "Finnhub API key not provided. News and fundamental data will be limited."
            )

    @api_retry_decorator(max_retries=3, initial_backoff=5)
    def get_technical_data(
        self, stock_code: str, market: str = "US", period: int = 200
    ) -> pd.DataFrame:
        """기술적 분석을 위한 OHLCV 데이터를 가져옵니다."""
        log.info(f"Fetching technical data for {stock_code} in {market} market...")
        if market.upper() == "US":
            try:
                ticker = yf.Ticker(stock_code)
                start_date = (datetime.now() - timedelta(days=period + 50)).strftime(
                    "%Y-%m-%d"
                )
                df = ticker.history(start=start_date, interval="1d")
                if df.empty:
                    log.warning(f"yfinance returned no data for {stock_code}.")
                    return None
                df = df[["Open", "High", "Low", "Close", "Volume"]]
                df.index = df.index.tz_localize(None)
                return df.tail(period)
            except Exception as e:
                raise IOError(f"yfinance failed for {stock_code}: {e}") from e
        elif market.upper() == "KR":
            return self.kis_wrapper.fetch_historical_data(
                stock_code, market, "D", period
            )
        else:
            raise ValueError(f"Unsupported market for technical data: {market}")

    @api_retry_decorator()
    def get_company_news(self, stock_code: str, start_date: str, end_date: str) -> list:
        """Finnhub에서 특정 기업의 뉴스를 가져옵니다."""
        if not self.finnhub_client:
            return []
        return self.finnhub_client.company_news(
            stock_code, _from=start_date, to=end_date
        )

    @api_retry_decorator()
    def get_fundamental_data(self, stock_code: str) -> dict:
        """Finnhub에서 기업 프로필(재무 정보 포함)을 가져옵니다."""
        if not self.finnhub_client:
            return {}
        return self.finnhub_client.company_profile2(symbol=stock_code)

    @api_retry_decorator()
    def get_general_market_news(self, category="general") -> list:
        """Finnhub에서 시장 전체 뉴스를 가져옵니다."""
        if not self.finnhub_client:
            return []
        return self.finnhub_client.general_news(category, min_id=0)

    def get_vix_index(self) -> float:
        """yfinance에서 실제 VIX 지수를 가져옵니다. 실패 시 기본값 20.0을 반환합니다."""
        for attempt in range(3):
            try:
                vix = yf.Ticker("^VIX")
                data = vix.history(period="1d")
                if not data.empty and "Close" in data.columns:
                    log.info(f"Successfully fetched VIX index: {data['Close'].iloc[-1]}")
                    return data["Close"].iloc[-1]
                log.warning(
                    f"Attempt {attempt + 1}/3: yfinance returned no data for VIX."
                )
            except Exception as e:
                log.warning(
                    f"Attempt {attempt + 1}/3: Failed to fetch VIX data from yfinance: {e}"
                )
            time.sleep(2 * (attempt + 1))
        log.error(
            "Failed to fetch VIX data after 3 attempts. Returning default value 20.0."
        )
        return 20.0

    def get_market_index(self, index_name: str = "S&P500") -> float:
        """yfinance에서 실제 S&P 500 지수를 가져옵니다. 실패 시 기본값 5000.0을 반환합니다."""
        for attempt in range(3):
            try:
                gspc = yf.Ticker("^GSPC")
                data = gspc.history(period="1d")
                if not data.empty and "Close" in data.columns:
                    log.info(
                        f"Successfully fetched S&P500 index: {data['Close'].iloc[-1]}"
                    )
                    return data["Close"].iloc[-1]
                log.warning(
                    f"Attempt {attempt + 1}/3: yfinance returned no data for S&P500."
                )
            except Exception as e:
                log.warning(
                    f"Attempt {attempt + 1}/3: Failed to fetch S&P500 data from yfinance: {e}"
                )
            time.sleep(2 * (attempt + 1))
        log.error(
            "Failed to fetch S&P500 data after 3 attempts. Returning default value 5000.0."
        )
        return 5000.0

    @api_retry_decorator(max_retries=3)
    def get_news_from_naver(self, stock_name: str) -> list:
        """
        Naver 검색 API를 이용해 특정 종목에 대한 최신 뉴스를 가져옵니다.
        """
        log.info(f"Naver에서 '{stock_name}' 관련 뉴스를 검색합니다...")
        if not config.NAVER_CLIENT_ID or not config.NAVER_CLIENT_SECRET:
            log.warning(
                "Naver API 키가 설정되지 않았습니다. Naver 뉴스 수집을 건너뜁니다."
            )
            return []

        try:
            url = "https://openapi.naver.com/v1/search/news.json"
            headers = {
                "X-Naver-Client-Id": config.NAVER_CLIENT_ID,
                "X-Naver-Client-Secret": config.NAVER_CLIENT_SECRET,
            }
            params = {
                "query": stock_name,
                "display": 10,
                "sort": "sim",
            }

            response = requests.get(url, headers=headers, params=params, timeout=5)
            response.raise_for_status()
            news_data = response.json()

            formatted_news = []
            for item in news_data.get("items", []):
                link = item.get("link")
                unique_id = abs(hash(link)) & (2**31 - 1)

                formatted_news.append(
                    {
                        "id": unique_id,
                        "category": "general",
                        "datetime": int(
                            datetime.strptime(
                                item.get("pubDate"), "%a, %d %b %Y %H:%M:%S +0900"
                            ).timestamp()
                        ),
                        "headline": item.get("title")
                        .replace("&quot;", '"')
                        .replace("<b>", "")
                        .replace("</b>", ""),
                        "summary": item.get("description")
                        .replace("&quot;", '"')
                        .replace("<b>", "")
                        .replace("</b>", ""),
                        "url": link,
                        "source": "Naver News",
                    }
                )
            return formatted_news
        except Exception as e:
            log.error(f"Naver 뉴스 검색 중 오류 발생 ({stock_name}): {e}")
            return []
