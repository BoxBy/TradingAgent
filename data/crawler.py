import requests
import time
from datetime import datetime
import finnhub
import yfinance as yf
from config import get_api_key
from data.kis_data import KISDataFetcher

class MarketCrawler:
    """
    Standalone crawler to ingest market data and news, fully replacing the old DataIngestion.
    """
    def __init__(self):
        self.naver_client_id = get_api_key("NAVER_CLIENT_ID")
        self.naver_client_secret = get_api_key("NAVER_CLIENT_SECRET")
        finnhub_key = get_api_key("FINNHUB_API_KEY")
        self.finnhub_client = finnhub.Client(api_key=finnhub_key) if finnhub_key else None

        if not self.naver_client_id or not self.naver_client_secret:
            print("[Warning] Naver API keys missing. KR market news will fail.")
        if not self.finnhub_client:
            print("[Warning] Finnhub API key missing. Fundamental data will be limited.")

    def get_news_from_naver(self, stock_name: str) -> list:
        """Fetch news from Naver via API matching old format."""
        if not self.naver_client_id or not self.naver_client_secret:
            return []
            
        url = "https://openapi.naver.com/v1/search/news.json"
        headers = {
            "X-Naver-Client-Id": self.naver_client_id,
            "X-Naver-Client-Secret": self.naver_client_secret,
        }
        params = {"query": stock_name, "display": 10, "sort": "sim"}

        try:
            response = requests.get(url, headers=headers, params=params, timeout=5)
            response.raise_for_status()
            data = response.json()
            
            formatted_news = []
            for item in data.get("items", []):
                link = item.get("link")
                unique_id = abs(hash(link)) & (2**31 - 1)
                
                # Using the exact same strptime parsing logic from TradingAgent
                pub_date = int(datetime.strptime(item.get("pubDate"), "%a, %d %b %Y %H:%M:%S +0900").timestamp())
                
                formatted_news.append({
                    "id": unique_id,
                    "category": "general",
                    "datetime": pub_date,
                    "headline": item.get("title").replace("&quot;", '"').replace("<b>", "").replace("</b>", ""),
                    "summary": item.get("description").replace("&quot;", '"').replace("<b>", "").replace("</b>", ""),
                    "url": link,
                    "source": "Naver News"
                })
            return formatted_news
        except Exception as e:
            print(f"[Crawler Error] Failed Naver Search for {stock_name}: {e}")
            return []
            
    def get_company_news(self, stock_code: str, start_date: str, end_date: str) -> list:
        """Fetch news from Finnhub, fallback to yfinance / Naver."""
        if self.finnhub_client:
            try:
                news = self.finnhub_client.company_news(stock_code, _from=start_date, to=end_date)
                if news:
                    return news
            except Exception as e:
                print(f"[Crawler Warning] Finnhub company news failed for {stock_code}: {e}")

        # Fallback to general search if it looks like a Korean code
        stock_str = str(stock_code)
        if stock_str.isdigit() or stock_str.endswith(".KS") or stock_str.endswith(".KQ"):
            # Simple assumption; ideally we'd map via ticker_utils
            return self.get_news_from_naver(stock_str)

        try:
            from core.ticker_utils import format_ticker_for_yfinance
            formatted_ticker = format_ticker_for_yfinance(stock_code)
            stock = yf.Ticker(formatted_ticker)
            raw_news = stock.news
            formatted_news = []
            for n in raw_news[:5]:
                formatted_news.append({
                    "id": n.get("uuid"),
                    "category": "company",
                    "datetime": n.get("providerPublishTime"),
                    "headline": n.get("title", ""),
                    "summary": "N/A", # yfinance often lacks summary in list
                    "url": n.get("link", ""),
                    "source": n.get("publisher", "yfinance")
                })
            return formatted_news
        except Exception as e:
            print(f"[Crawler Error] Failed yfinance News for {stock_code}: {e}")
            return []
            
    def get_general_market_news(self, category="general") -> list:
        """Fetch general market news."""
        if self.finnhub_client:
            try:
                return self.finnhub_client.general_news(category, min_id=0)
            except Exception as e:
                print(f"[Crawler Warning] Finnhub general news failed: {e}")
                
        return self.get_news_from_naver("증시")

    def get_vix_index(self) -> float:
        for attempt in range(3):
            try:
                vix = yf.Ticker("^VIX")
                data = vix.history(period="1d")
                if not data.empty and "Close" in data.columns:
                    return data["Close"].iloc[-1]
            except Exception:
                pass
            time.sleep(2 * (attempt + 1))
        return 20.0

    def get_market_index(self, index_name: str = "S&P500") -> float:
        for attempt in range(3):
            try:
                gspc = yf.Ticker("^GSPC")
                data = gspc.history(period="1d")
                if not data.empty and "Close" in data.columns:
                    return data["Close"].iloc[-1]
            except Exception:
                pass
            time.sleep(2 * (attempt + 1))
        return 5000.0

    def get_fundamental_data(self, stock_code: str) -> dict:
        """
        Fetch fundamental data from Finnhub, fallback to KIS(KR) or yfinance(US).
        """
        if self.finnhub_client:
            try:
                data = self.finnhub_client.company_profile2(symbol=stock_code)
                if data:
                    return data
            except Exception as e:
                print(f"[Crawler Warning] Finnhub fundamental data failed for {stock_code}: {e}")

        return self._get_fundamental_fallback(stock_code)

    def _get_fundamental_fallback(self, stock_code: str) -> dict:
        stock_code_str = str(stock_code)
        is_kr = stock_code_str.isdigit() or stock_code_str.endswith(".KS") or stock_code_str.endswith(".KQ")
        
        if is_kr:
            try:
                kis_fetcher = KISDataFetcher()
                code = stock_code.split(".")[0]
                
                basic_info = kis_fetcher.get_stock_basic_info(code) or {}
                if not basic_info:
                    return {}
                
                financial_ratios = {}
                balance_sheet = {}
                income_statement = {}
                
                for api_name, api_func in [
                    ("financial_ratios", lambda: kis_fetcher.get_financial_ratios(code)),
                    ("balance_sheet", lambda: kis_fetcher.get_balance_sheet(code)),
                    ("income_statement", lambda: kis_fetcher.get_income_statement(code)),
                ]:
                    try:
                        time.sleep(0.2)
                        result = api_func()
                        if result:
                            if api_name == "financial_ratios": financial_ratios = result
                            elif api_name == "balance_sheet": balance_sheet = result
                            else: income_statement = result
                    except Exception as e:
                        pass
                
                combined_data = {
                    **basic_info,
                    "financial_ratios": financial_ratios,
                    "balance_sheet": balance_sheet,
                    "income_statement": income_statement,
                }
                return self._normalize_fundamental_data(combined_data, "KR")
            except Exception as e:
                print(f"[Crawler Error] KIS fundamental fallback failed for {stock_code}: {e}")
        else:
            try:
                ticker = yf.Ticker(stock_code)
                info = ticker.info
                return self._normalize_fundamental_data(info, "US")
            except Exception as e:
                print(f"[Crawler Error] yfinance fundamental fallback failed for {stock_code}: {e}")
        
        return {}

    def _normalize_fundamental_data(self, data: dict, source_type: str) -> dict:
        normalized = {}
        if source_type == "KR":
            normalized["ticker"] = data.get("stck_shrn_iscd") or data.get("FID_INPUT_ISCD") or data.get("bstp_cls_code", "")
            normalized["name"] = data.get("rprs_co_nm_kora") or data.get("hts_kor_isnm") or normalized["ticker"]
            
            try: price = float(data.get("stck_prpr", 0))
            except: price = 0

            financial_ratios_list = data.get("financial_ratios", [])
            latest_ratios = financial_ratios_list[0] if isinstance(financial_ratios_list, list) and financial_ratios_list else {}
            
            income_statement_list = data.get("income_statement", [])
            latest_income = income_statement_list[0] if isinstance(income_statement_list, list) and income_statement_list else {}

            balance_sheet_list = data.get("balance_sheet", [])
            latest_balance = balance_sheet_list[0] if isinstance(balance_sheet_list, list) and balance_sheet_list else {}

            try:
                normalized["per"] = float(data.get("per", 0) or 0)
                normalized["pbr"] = float(data.get("pbr", 0) or 0)
                normalized["marketCapitalization"] = float(data.get("hts_avls", 0) or 0) * 100 
            except:
                normalized["per"] = 0
                normalized["pbr"] = 0
                normalized["marketCapitalization"] = 0

            if normalized["per"] == 0 or normalized["pbr"] == 0 or normalized["marketCapitalization"] == 0:
                try:
                    eps = float(latest_ratios.get("eps", 0))
                    bps = float(latest_ratios.get("bps", 0))
                    roe = float(latest_ratios.get("roe_val", 0))
                    net_income_100m = float(latest_income.get("thtr_ntin", 0))
                except:
                    eps = bps = roe = net_income_100m = 0

                if normalized["per"] == 0 and eps > 0 and price > 0:
                    normalized["per"] = price / eps
                if normalized["pbr"] == 0 and bps > 0 and price > 0:
                    normalized["pbr"] = price / bps
                if normalized["marketCapitalization"] == 0 and eps > 0 and net_income_100m != 0:
                    shares = (net_income_100m * 100000000) / eps
                    normalized["marketCapitalization"] = (shares * price) / 1000000

            try: normalized["roe"] = float(latest_ratios.get("roe_val", 0))
            except: normalized["roe"] = 0

            try:
                cras = float(latest_balance.get("cras", 0))
                flow_lblt = float(latest_balance.get("flow_lblt", 0))
                normalized["current_ratio"] = (cras / flow_lblt) * 100 if flow_lblt > 0 else 0
                normalized["debt_ratio"] = float(latest_ratios.get("lblt_rate", 0))
            except:
                normalized["current_ratio"] = 0
                normalized["debt_ratio"] = 0

            try:
                normalized["total_assets"] = float(latest_balance.get("total_aset", 0))
                normalized["total_liabilities"] = float(latest_balance.get("total_lblt", 0))
                normalized["total_equity"] = float(latest_balance.get("total_cptl", 0))
                normalized["revenue"] = float(latest_income.get("sale_account", 0)) * 100
                normalized["operating_income"] = float(latest_income.get("op_prfi", 0)) * 100
                normalized["net_income"] = float(latest_income.get("thtr_ntin", 0)) * 100
            except: pass

            normalized["shareOutstanding"] = 0
            normalized["country"] = "KR"
            normalized["currency"] = "KRW"
            normalized["finnhubIndustry"] = data.get("bstp_kor_isnm", "N/A")
            
        elif source_type == "US":
            normalized["ticker"] = data.get("symbol", "")
            normalized["name"] = data.get("longName") or data.get("shortName", "N/A")
            normalized["marketCapitalization"] = (data.get("marketCap", 0) or 0) / 1000000
            normalized["shareOutstanding"] = (data.get("sharesOutstanding", 0) or 0) / 1000000
            normalized["country"] = data.get("country", "US")
            normalized["currency"] = data.get("currency", "USD")
            normalized["finnhubIndustry"] = data.get("industry", "N/A")
            normalized["weburl"] = data.get("website", "")
            normalized["logo"] = data.get("logo_url", "")
            
            normalized["per"] = data.get("trailingPE") or 0
            normalized["pbr"] = data.get("priceToBook") or 0
            normalized["roe"] = data.get("returnOnEquity") or 0
            normalized["roa"] = data.get("returnOnAssets") or 0
            normalized["current_ratio"] = data.get("currentRatio") or 0
            normalized["debt_ratio"] = data.get("debtToEquity") or 0
            
            normalized["total_assets"] = data.get("totalAssets", 0) or 0
            normalized["total_liabilities"] = data.get("totalLiab", 0) or 0
            normalized["total_equity"] = data.get("totalStockholderEquity", 0) or 0
            normalized["revenue"] = data.get("totalRevenue", 0) or 0
            normalized["operating_income"] = data.get("operatingCashflow", 0) or 0
            normalized["net_income"] = data.get("netIncomeToCommon", 0) or 0
            
        return normalized
