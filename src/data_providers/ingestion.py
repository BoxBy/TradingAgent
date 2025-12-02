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
                df = ticker.history(period="1d", interval="15m")
                if df.empty:
                    log.warning(f"yfinance returned no data for {stock_code}.")
                    return None
                df = df[["Open", "High", "Low", "Close", "Volume"]]
                df.index = df.index.tz_localize(None)
                return df
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
        """
        Finnhub에서 특정 기업의 뉴스를 가져옵니다.
        실패 시 yfinance(US) 또는 Naver(KR)로 대체합니다.
        """
        news = []
        # 1. Try Finnhub
        if self.finnhub_client:
            try:
                news = self.finnhub_client.company_news(
                    stock_code, _from=start_date, to=end_date
                )
                if news:
                    return news
            except Exception as e:
                log.warning(f"Finnhub company news failed for {stock_code}: {e}")

        # 2. Fallback
        log.info(f"Using fallback news source for {stock_code}")
        # KR Stock -> Naver
        if stock_code.isdigit() or stock_code.endswith(".KS") or stock_code.endswith(".KQ"):
             # Naver news is already implemented in get_news_from_naver
             # We need to map the stock code to name first, but get_news_from_naver takes name.
             # We can try to use the utility to get name.
             from ..utils import ticker_utils
             stock_name = ticker_utils.get_stock_name(stock_code)
             if stock_name:
                 return self.get_news_from_naver(stock_name)
        
        # US Stock -> yfinance (Limited)
        try:
            ticker = yf.Ticker(stock_code)
            # yfinance news is unstructured, but we can try to format it
            yf_news = ticker.news
            formatted_news = []
            for item in yf_news:
                formatted_news.append({
                    "id": item.get("uuid"),
                    "category": "company",
                    "datetime": item.get("providerPublishTime"),
                    "headline": item.get("title"),
                    "summary": "N/A", # yfinance often doesn't provide summary in list
                    "url": item.get("link"),
                    "source": item.get("publisher"),
                })
            return formatted_news
        except Exception as e:
            log.warning(f"yfinance news fallback failed for {stock_code}: {e}")
        
        return []

    @api_retry_decorator()
    def get_fundamental_data(self, stock_code: str) -> dict:
        """
        Finnhub에서 기업 프로필(재무 정보 포함)을 가져옵니다.
        실패 시 KIS API(KR) 또는 yfinance(US)로 대체합니다.
        """
        # 1. Try Finnhub
        if self.finnhub_client:
            try:
                data = self.finnhub_client.company_profile2(symbol=stock_code)
                if data:
                    return data
            except Exception as e:
                log.warning(f"Finnhub fundamental data failed for {stock_code}: {e}")

        # 2. Fallback
        log.info(f"Using fallback fundamental data for {stock_code}")
        return self._get_fundamental_fallback(stock_code)

    def _get_fundamental_fallback(self, stock_code: str) -> dict:
        """
        Fallback implementation for fundamental data
        Phase 1: 기본 정보 우선, 재무 정보는 선택적 (Rate limit 방지)
        """
        # Determine market
        is_kr = stock_code.isdigit() or stock_code.endswith(".KS") or stock_code.endswith(".KQ")
        
        if is_kr:
            # KIS API - 기본 정보 우선, 재무 정보는 선택적
            try:
                # Remove suffix for KIS API if needed
                code = stock_code.split(".")[0]
                
                # 1. 주식기본조회 (필수) - 기본 정보만이라도 확보
                basic_info = {}
                try:
                    basic_info = self.kis_wrapper.get_stock_basic_info(code) or {}
                except Exception as e:
                    log.warning(f"Basic info failed for {code}: {e}")
                    # 기본 정보도 실패하면 빈 dict라도 반환
                    return {}
                
                # 기본 정보만 있어도 정규화 가능
                if not basic_info:
                    log.warning(f"No basic info available for {code}")
                    return {}
                
                # 2-4. 재무 정보 (선택적) - 실패해도 계속 진행
                # Rate limit 방지를 위해 각 호출 사이 지연 추가
                financial_ratios = {}
                balance_sheet = {}
                income_statement = {}
                
                # 재무 정보는 독립적으로 시도 (하나 실패해도 다른 것 계속 시도)
                # Rate limit 방지: 각 API 호출 사이 0.2초 대기
                for api_name, api_func in [
                    ("financial_ratios", lambda: self.kis_wrapper.get_financial_ratios(code)),
                    ("balance_sheet", lambda: self.kis_wrapper.get_balance_sheet(code)),
                    ("income_statement", lambda: self.kis_wrapper.get_income_statement(code)),
                ]:
                    try:
                        time.sleep(0.2)  # Rate limit 방지: 초당 19회 제한 = 약 0.05초 간격, 안전 마진 포함
                        result = api_func()
                        if result:
                            if api_name == "financial_ratios":
                                financial_ratios = result
                            elif api_name == "balance_sheet":
                                balance_sheet = result
                            else:
                                income_statement = result
                    except Exception as e:
                        # 재무 정보 실패는 경고만 (비중요)
                        log.debug(f"{api_name} failed for {code} (non-critical): {e}")
                
                # 통합 데이터 구성 (기본 정보 + 선택적 재무 정보)
                combined_data = {
                    **basic_info,  # 기본 정보를 베이스로
                    "financial_ratios": financial_ratios,
                    "balance_sheet": balance_sheet,
                    "income_statement": income_statement,
                }
                
                return self._normalize_fundamental_data(combined_data, "KR")
            except Exception as e:
                log.error(f"KIS fundamental fallback failed for {stock_code}: {e}")
        else:
            # yfinance (US)
            try:
                ticker = yf.Ticker(stock_code)
                info = ticker.info
                return self._normalize_fundamental_data(info, "US")
            except Exception as e:
                log.error(f"yfinance fundamental fallback failed for {stock_code}: {e}")
        
        return {}

    def _normalize_fundamental_data(self, data: dict, source_type: str) -> dict:
        """
        KIS/yfinance 데이터를 Finnhub company_profile2 형식으로 변환합니다.
        Phase 1: 기본 정보 + 재무비율 + 대차대조표 + 손익계산서 통합
        
        Target fields: country, currency, exchange, ipo, marketCapitalization, 
                       name, phone, shareOutstanding, ticker, weburl, logo, finnhubIndustry
        """
        normalized = {}
        
        if source_type == "KR":
            # KIS Data 통합 (기본정보 + 재무비율 + 대차대조표 + 손익계산서)
            # 기본 정보 (FHPST01010000 output)
            normalized["ticker"] = data.get("stck_shrn_iscd") or data.get("FID_INPUT_ISCD") or data.get("bstp_cls_code", "")
            # Name is missing in FHPST01010000, try to use what we have or N/A
            normalized["name"] = data.get("rprs_co_nm_kora") or data.get("hts_kor_isnm") or normalized["ticker"]
            
            # Extract Price
            try:
                price = float(data.get("stck_prpr", 0))
            except (ValueError, TypeError):
                price = 0

            # 재무비율에서 추출 (Latest)
            financial_ratios_list = data.get("financial_ratios", [])
            latest_ratios = {}
            if isinstance(financial_ratios_list, list) and financial_ratios_list:
                latest_ratios = financial_ratios_list[0]  # Assume sorted descending
            
            # 손익계산서에서 추출 (Latest)
            income_statement_list = data.get("income_statement", [])
            latest_income = {}
            if isinstance(income_statement_list, list) and income_statement_list:
                latest_income = income_statement_list[0]

            # 대차대조표에서 추출 (Latest)
            balance_sheet_list = data.get("balance_sheet", [])
            latest_balance = {}
            if isinstance(balance_sheet_list, list) and balance_sheet_list:
                latest_balance = balance_sheet_list[0]

            # 1. PER, PBR, Market Cap from Basic Info (Priority)
            try:
                normalized["per"] = float(data.get("per", 0) or 0)
                normalized["pbr"] = float(data.get("pbr", 0) or 0)
                # hts_avls is in 100 Million KRW
                normalized["marketCapitalization"] = float(data.get("hts_avls", 0) or 0) * 100 
            except:
                normalized["per"] = 0
                normalized["pbr"] = 0
                normalized["marketCapitalization"] = 0

            # 2. Fallback Calculation if API values are 0
            if normalized["per"] == 0 or normalized["pbr"] == 0 or normalized["marketCapitalization"] == 0:
                try:
                    eps = float(latest_ratios.get("eps", 0))
                    bps = float(latest_ratios.get("bps", 0))
                    roe = float(latest_ratios.get("roe_val", 0))
                    net_income_100m = float(latest_income.get("thtr_ntin", 0))
                except:
                    eps = 0
                    bps = 0
                    roe = 0
                    net_income_100m = 0

                if normalized["per"] == 0 and eps > 0 and price > 0:
                    normalized["per"] = price / eps
                
                if normalized["pbr"] == 0 and bps > 0 and price > 0:
                    normalized["pbr"] = price / bps
                    
                if normalized["marketCapitalization"] == 0 and eps > 0 and net_income_100m != 0:
                    shares = (net_income_100m * 100000000) / eps
                    normalized["marketCapitalization"] = (shares * price) / 1000000

            # ROE is usually not in basic info, get from ratios
            try:
                normalized["roe"] = float(latest_ratios.get("roe_val", 0))
            except:
                normalized["roe"] = 0

            # Other Ratios
            try:
                normalized["current_ratio"] = float(latest_ratios.get("lblt_rate", 0)) # Using Debt Ratio as proxy? No.
                # KIS doesn't explicitly provide Current Ratio in this TR. 
                # We can calculate if we had Current Assets/Liabilities.
                # Balance Sheet has 'cras' (Current Assets) and 'flow_lblt' (Current Liabilities).
                cras = float(latest_balance.get("cras", 0))
                flow_lblt = float(latest_balance.get("flow_lblt", 0))
                if flow_lblt > 0:
                    normalized["current_ratio"] = (cras / flow_lblt) * 100
                else:
                    normalized["current_ratio"] = 0
                    
                normalized["debt_ratio"] = float(latest_ratios.get("lblt_rate", 0))
            except:
                normalized["current_ratio"] = 0
                normalized["debt_ratio"] = 0

            # Financial Statements
            try:
                normalized["total_assets"] = float(latest_balance.get("total_aset", 0))
                normalized["total_liabilities"] = float(latest_balance.get("total_lblt", 0))
                normalized["total_equity"] = float(latest_balance.get("total_cptl", 0))
                
                normalized["revenue"] = float(latest_income.get("sale_account", 0)) * 100000000 # 100 Million -> Won? Finnhub expects Million?
                # Finnhub revenue is usually in Million.
                # KIS sale_account is in 100 Million.
                # So * 100 to get Million.
                normalized["revenue"] = float(latest_income.get("sale_account", 0)) * 100
                normalized["operating_income"] = float(latest_income.get("op_prfi", 0)) * 100
                normalized["net_income"] = float(latest_income.get("thtr_ntin", 0)) * 100
            except:
                pass

            # Basic Fields
            normalized["shareOutstanding"] = 0 # Calculated implicitly above but not stored
            normalized["country"] = "KR"
            normalized["currency"] = "KRW"
            normalized["finnhubIndustry"] = data.get("bstp_kor_isnm", "N/A")
            
            # Raw Data
            normalized["_raw_basic_info"] = {k: v for k, v in data.items() 
                                            if k not in ["financial_ratios", "balance_sheet", "income_statement"]}
            normalized["_raw_financial_ratios"] = financial_ratios_list
            normalized["_raw_balance_sheet"] = balance_sheet_list
            normalized["_raw_income_statement"] = income_statement_list
            
        elif source_type == "US":
            # yfinance info mapping
            normalized["ticker"] = data.get("symbol", "")
            normalized["name"] = data.get("longName") or data.get("shortName", "N/A")
            normalized["marketCapitalization"] = (data.get("marketCap", 0) or 0) / 1000000  # Convert to Million
            normalized["shareOutstanding"] = (data.get("sharesOutstanding", 0) or 0) / 1000000
            normalized["country"] = data.get("country", "US")
            normalized["currency"] = data.get("currency", "USD")
            normalized["finnhubIndustry"] = data.get("industry", "N/A")
            normalized["weburl"] = data.get("website", "")
            normalized["logo"] = data.get("logo_url", "")
            
            # 재무 지표
            normalized["per"] = data.get("trailingPE") or 0
            normalized["pbr"] = data.get("priceToBook") or 0
            normalized["roe"] = data.get("returnOnEquity") or 0
            normalized["roa"] = data.get("returnOnAssets") or 0
            normalized["current_ratio"] = data.get("currentRatio") or 0
            normalized["debt_ratio"] = data.get("debtToEquity") or 0
            
            # 재무 정보
            normalized["total_assets"] = data.get("totalAssets", 0) or 0
            normalized["total_liabilities"] = data.get("totalLiab", 0) or 0
            normalized["total_equity"] = data.get("totalStockholderEquity", 0) or 0
            normalized["revenue"] = data.get("totalRevenue", 0) or 0
            normalized["operating_income"] = data.get("operatingCashflow", 0) or 0
            normalized["net_income"] = data.get("netIncomeToCommon", 0) or 0
            
        return normalized

    @api_retry_decorator()
    def get_general_market_news(self, category="general") -> list:
        """
        Finnhub에서 시장 전체 뉴스를 가져옵니다.
        실패 시 Naver 뉴스(KR) 또는 빈 리스트로 대체합니다.
        """
        # 1. Try Finnhub
        if self.finnhub_client:
            try:
                return self.finnhub_client.general_news(category, min_id=0)
            except Exception as e:
                log.warning(f"Finnhub general news failed: {e}")

        # 2. Fallback
        # General market news is hard to replace 1:1.
        # We can return empty list or try to fetch some general keywords from Naver.
        log.info("Using fallback for general market news (Naver '증시')")
        try:
            return self.get_news_from_naver("증시")
        except:
            return []


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
