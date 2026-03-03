import os
import sys
import requests
import time
import json
from functools import wraps
from config import get_api_key, MOCK_TRADING

class KISRateLimiter:
    """Rate limiter for KIS API. Mock: 2 req/sec, Real: 20 req/sec."""
    def __init__(self, rps: float = 2.0):
        self.interval = 1.0 / rps
        self.last_call = 0.0

    def __call__(self, func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            elapsed = time.monotonic() - self.last_call
            wait = self.interval - elapsed
            if wait > 0:
                time.sleep(wait)
            result = func(*args, **kwargs)
            self.last_call = time.monotonic()
            return result
        return wrapper

# Global rate limiter instance (Mock is 1 req/sec)
_kis_rps = 1.0 if MOCK_TRADING else 19.0
kis_rate_limit = KISRateLimiter(rps=_kis_rps)

class KISOfficialClient:
    """
    Official KIS OpenAPI HTTP Wrapper. Direct integration via `requests`.
    Eliminates dependency on the unofficial `pykis` library.
    """
    def __init__(self):
        self.mock = MOCK_TRADING
        self.app_key = get_api_key("KIS_MOCK_APP_KEY") if self.mock else get_api_key("KIS_APP_KEY")
        self.app_secret = get_api_key("KIS_MOCK_APP_SECRET") if self.mock else get_api_key("KIS_APP_SECRET")
        self.account_no = get_api_key("KIS_MOCK_ACCOUNT_NO") if self.mock else get_api_key("KIS_ACCOUNT_NO")
        
        # Valid domain is openapivts (Virtual Trading System)
        self.domain = "https://openapivts.koreainvestment.com:29443" if self.mock else "https://openapi.koreainvestment.com:9443"
        
        self.token_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".kis_token.json")
        self.access_token = None
        self.token_expiry = 0
        self._load_cached_token()
        
        if not self.access_token or time.time() >= self.token_expiry:
            self._init_token()
        print(f"[KIS] 시스템 준비 완료 (Mock={self.mock})", file=sys.stderr)

    def _load_cached_token(self):
        """Load token from local file if available."""
        if os.path.exists(self.token_file):
            try:
                with open(self.token_file, "r") as f:
                    data = json.load(f)
                    self.access_token = data.get("access_token")
                    self.token_expiry = data.get("token_expiry", 0)
                    if self.access_token and time.time() < self.token_expiry:
                        print("[KIS] 캐시된 토큰 로드 완료", file=sys.stderr)
                    else:
                        print("[KIS] 케시 토큰 만료 - 재발급 필요", file=sys.stderr)
            except:
                pass

    def _save_cached_token(self):
        """Save token to local file."""
        try:
            with open(self.token_file, "w") as f:
                json.dump({
                    "access_token": self.access_token,
                    "token_expiry": self.token_expiry
                }, f)
        except:
            pass

    def _init_token(self):
        """Generate OAuth2 token from KIS"""
        url = f"{self.domain}/oauth2/tokenP"
        headers = {
            "content-type": "application/json",
            "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        }
        body = {
            "grant_type": "client_credentials",
            "appkey": self.app_key,
            "appsecret": self.app_secret
        }
        
        # Debugging: Masked keys
        masked_key = f"{self.app_key[:4]}...{self.app_key[-4:]}" if self.app_key else "None"
        
        for attempt in range(3):
            print(f"[KIS] 토큰 요청 중... (Attempt {attempt+1})", file=sys.stderr)
            res = requests.post(url, headers=headers, json=body)
            
            if res.status_code == 200:
                data = res.json()
                self.access_token = data.get("access_token")
                self.token_expiry = time.time() + int(data.get("expires_in", 86400)) - 60
                self._save_cached_token()
                print("[KIS] ✅ OAuth2 토큰 발급 성공", file=sys.stderr)
                return
            
            # EGW00133: Issued too frequently (1 per minute)
            if "EGW00133" in res.text and attempt < 2:
                print(f"[KIS] ⚠️  토큰 발급 주기 제한. 20초 대기... ({attempt+1}/3)", file=sys.stderr)
                time.sleep(20)
                continue
            
            print(f"[KIS] ❌ 토큰 발급 실패 ({res.status_code}): {res.text}", file=sys.stderr)
            res.raise_for_status()

    def _get_headers(self, tr_id: str) -> dict:
        if not self.access_token or time.time() >= self.token_expiry:
            self._init_token()
            
        return {
            "content-type": "application/json; charset=utf-8",
            "authorization": f"Bearer {self.access_token}",
            "appkey": self.app_key,
            "appsecret": self.app_secret,
            "tr_id": tr_id,
            "custtype": "P",
            "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        }

    # === Fundamentals & Read-Only (Equivalent to kis_data.py) ===
    
    @kis_rate_limit
    def get_kr_current_price(self, code: str) -> float:
        """Fetch current price of KR stock (FHKST01010100)"""
        url = f"{self.domain}/uapi/domestic-stock/v1/quotations/inquire-price"
        headers = self._get_headers("FHKST01010100")
        params = {"FID_COND_MRKT_DIV_CODE": "J", "FID_INPUT_ISCD": code}
        
        res = requests.get(url, headers=headers, params=params)
        if res.status_code == 200:
            out = res.json().get("output", {})
            return float(out.get("stck_prpr", 0))
        return 0.0

    @kis_rate_limit
    def get_financial_ratios(self, code: str) -> list:
        """FHKST66430300 or FHKST66430200 depending on API revision. Using standard one."""
        url = f"{self.domain}/uapi/domestic-stock/v1/finance/financial-ratio"
        headers = self._get_headers("FHKST66430200")
        params = {"FID_COND_MRKT_DIV_CODE": "J", "FID_INPUT_ISCD": code, "FID_DIV_CLS_CODE": "0"}
        res = requests.get(url, headers=headers, params=params)
        return res.json().get("output", []) if res.status_code == 200 else []

    @kis_rate_limit
    def get_balance_sheet(self, code: str) -> list:
        """FHKST66430100"""
        url = f"{self.domain}/uapi/domestic-stock/v1/finance/balance-sheet"
        headers = self._get_headers("FHKST66430100")
        params = {"FID_COND_MRKT_DIV_CODE": "J", "FID_INPUT_ISCD": code, "FID_DIV_CLS_CODE": "0"}
        res = requests.get(url, headers=headers, params=params)
        return res.json().get("output", []) if res.status_code == 200 else []

    @kis_rate_limit
    def get_income_statement(self, code: str) -> list:
        """FHKST66430300"""
        url = f"{self.domain}/uapi/domestic-stock/v1/finance/income-statement"
        headers = self._get_headers("FHKST66430300")
        params = {"FID_COND_MRKT_DIV_CODE": "J", "FID_INPUT_ISCD": code, "FID_DIV_CLS_CODE": "0"}
        res = requests.get(url, headers=headers, params=params)
        return res.json().get("output", []) if res.status_code == 200 else []

    # === Trading & Balance (Equivalent to the MCP Bridge) ===
    
    @kis_rate_limit
    def get_buyable_cash(self) -> float:
        """Get KR cash from inquire-balance output2 (dnca_tot_amt)"""
        url = f"{self.domain}/uapi/domestic-stock/v1/trading/inquire-balance"
        tr_id = "VTTC8434R" if self.mock else "TTTC8434R"

        cano = self.account_no.split("-")[0] if "-" in self.account_no else self.account_no[:8]
        acnt_prdt_cd = self.account_no.split("-")[1] if "-" in self.account_no else self.account_no[8:]

        params = {
            "CANO": cano,
            "ACNT_PRDT_CD": acnt_prdt_cd,
            "AFHR_FLPR_YN": "N",
            "OFL_YN": "N",
            "INQR_DVSN": "01",
            "UNPR_DVSN": "01",
            "FUND_STTL_ICLD_YN": "N",
            "FNCG_AMT_AUTO_RDPT_YN": "N",
            "PRCS_DVSN": "01",
            "CTX_AREA_FK100": "",
            "CTX_AREA_NK100": ""
        }

        headers = self._get_headers(tr_id)
        try:
            res = requests.get(url, headers=headers, params=params, timeout=15)
            if res.status_code == 200:
                data = res.json()
                with open("/tmp/kis_debug.json", "a") as f:
                    f.write(f"--- get_buyable_cash ---\n{json.dumps(data, indent=2, ensure_ascii=False)}\n")
                # output2[0]['dnca_tot_amt'] contains the deposit cash
                output2 = data.get("output2", [])
                if output2 and isinstance(output2, list) and len(output2) > 0:
                    return float(output2[0].get("dnca_tot_amt", 0))
            else:
                print(f"[KIS] get_buyable_cash HTTP {res.status_code}: {res.text}", file=sys.stderr)
        except Exception as e:
            print(f"[KIS] get_buyable_cash 실패: {e}", file=sys.stderr)
        return 0.0

    @kis_rate_limit
    def get_us_cash(self) -> float:
        """Fetch US foreign currency deposit (USD) (VTRP6504R / CTRP6504R)"""
        url = f"{self.domain}/uapi/overseas-stock/v1/trading/inquire-present-balance"
        tr_id = "VTRP6504R" if self.mock else "CTRP6504R"
        headers = self._get_headers(tr_id)

        cano = self.account_no.split("-")[0] if "-" in self.account_no else self.account_no[:8]
        acnt_prdt_cd = self.account_no.split("-")[1] if "-" in self.account_no else self.account_no[8:]

        params = {
            "CANO": cano,
            "ACNT_PRDT_CD": acnt_prdt_cd,
            "WCRC_FRCR_DVSN_CD": "02",
            "NATN_CD": "840",
            "TR_MKET_CD": "00",
            "INQR_DVSN_CD": "00"
        }
        try:
            res = requests.get(url, headers=headers, params=params, timeout=15)
            if res.status_code == 200:
                data = res.json()
                # Debug: save full response
                with open("/tmp/kis_debug.json", "a") as f:
                    f.write(f"--- get_us_cash ---\n{json.dumps(data, indent=2, ensure_ascii=False)}\n")

                # output2 is a list of currency rows - find USD row
                output2 = data.get("output2", [])
                if isinstance(output2, list) and len(output2) > 0:
                    # Find USD row
                    for row in output2:
                        if isinstance(row, dict) and row.get("crcy_cd") == "USD":
                            # Try multiple possible fields for USD cash
                            usd_candidates = [
                                "frcr_dncl_amt_2",       # 외화 대체예수금 (most reliable)
                                "frcr_sll_amt_smtl",     # 외화 매도금액 합계
                                "frcr_buy_amt_smtl",     # 외화 매수금액 합계
                                "frcr_drwg_psbl_amt_1",  # 외화 출금 가능 금액
                                "nxdy_frcr_drwg_psbl_amt", # 익일 출금 가능 금액
                            ]
                            for key in usd_candidates:
                                try:
                                    val = float(row.get(key, 0))
                                    if val > 0:
                                        print(f"[KIS] get_us_cash found {key}: {val}", file=sys.stderr)
                                        return val
                                except:
                                    pass
                            break
            else:
                print(f"[KIS] get_us_cash HTTP {res.status_code}: {res.text}", file=sys.stderr)
        except Exception as e:
            print(f"[KIS] get_us_cash 실패: {e}", file=sys.stderr)
        return 0.0

    @kis_rate_limit
    def get_us_total_assets_krw(self) -> float:
        """Get total overseas assets (portfolio + cash) in KRW from output3.frcr_evlu_tota"""
        url = f"{self.domain}/uapi/overseas-stock/v1/trading/inquire-present-balance"
        tr_id = "VTRP6504R" if self.mock else "CTRP6504R"
        headers = self._get_headers(tr_id)

        cano = self.account_no.split("-")[0] if "-" in self.account_no else self.account_no[:8]
        acnt_prdt_cd = self.account_no.split("-")[1] if "-" in self.account_no else self.account_no[8:]

        params = {
            "CANO": cano,
            "ACNT_PRDT_CD": acnt_prdt_cd,
            "WCRC_FRCR_DVSN_CD": "02",
            "NATN_CD": "840",
            "TR_MKET_CD": "00",
            "INQR_DVSN_CD": "00"
        }
        try:
            res = requests.get(url, headers=headers, params=params, timeout=15)
            if res.status_code == 200:
                data = res.json()
                # output3 contains total overseas assets in KRW
                output3 = data.get("output3", {})
                if isinstance(output3, dict):
                    # frcr_evlu_tota: 외화 평가 총액 (KRW 환산, 포트폴리오 + 현금)
                    total = float(output3.get("frcr_evlu_tota", 0))
                    if total > 0:
                        print(f"[KIS] get_us_total_assets_krw: {total:,.0f} KRW", file=sys.stderr)
                        return total
            else:
                print(f"[KIS] get_us_total_assets_krw HTTP {res.status_code}: {res.text}", file=sys.stderr)
        except Exception as e:
            print(f"[KIS] get_us_total_assets_krw 실패: {e}", file=sys.stderr)
        return 0.0

    @kis_rate_limit
    def get_exchange_rate(self) -> float:
        """Retrieve USD/KRW exchange rate. Falls back to 1350 if fails."""
        # Ticker-based approach for USD/KRW
        url = f"{self.domain}/uapi/overseas-stock/v1/quotations/inquire-daily-chartprice"
        headers = self._get_headers("FHKST03011100")
        params = {
            "FID_COND_MRKT_DIV_CODE": "U", "FID_INPUT_ISCD": "FX@USDKRW",
            "FID_PERIOD_DIV_CODE": "D", "FID_ORG_ADJ_PRC": "0000000000"
        }
        try:
            res = requests.get(url, headers=headers, params=params, timeout=5)
            if res.status_code == 200:
                out = res.json().get("output1", [{}])[0]
                rate = float(out.get("stck_clpr", 1350))
                if rate > 500: return rate
        except:
            pass
        return 1350.0 # Standard fallback

    @kis_rate_limit
    def get_portfolio(self) -> list:
        """Fetches KR (VTTC8434R) and US (VTTS3012R) portfolios and merges them."""
        pf = []
        cano = self.account_no.split("-")[0] if "-" in self.account_no else self.account_no[:8]
        acnt_prdt_cd = self.account_no.split("-")[1] if "-" in self.account_no else self.account_no[8:]

        # KR Portfolio - use TTTC8434R for real, VTTC8434R for mock (same as pykis)
        kr_url = f"{self.domain}/uapi/domestic-stock/v1/trading/inquire-balance"
        kr_tr_id = "VTTC8434R" if self.mock else "TTTC8434R"
        kr_headers = self._get_headers(kr_tr_id)
        kr_params = {
            "CANO": cano, "ACNT_PRDT_CD": acnt_prdt_cd,
            "AFHR_FLPR_YN": "N", "OFL_YN": "N", "INQR_DVSN": "01", "UNPR_DVSN": "01",
            "FUND_STTL_ICLD_YN": "N", "FNCG_AMT_AUTO_RDPT_YN": "N", "PRCS_DVSN": "01", "CTX_AREA_FK100": "", "CTX_AREA_NK100": ""
        }

        try:
            kr_res = requests.get(kr_url, headers=kr_headers, params=kr_params, timeout=15)
            if kr_res.status_code == 200:
                for item in kr_res.json().get("output1", []):
                    qty = float(item.get("hldg_qty", 0))
                    if qty > 0:
                        pf.append({
                            "stock_code": item.get("pdno"),
                            "quantity": qty,
                            "current_price": float(item.get("prpr", 0)),
                            "average_price": float(item.get("pchs_avg_pric", 0)),
                            "pnl_percent": float(item.get("evlu_pfls_rt", 0)),
                            "market_type": "KR"
                        })
        except Exception as e:
            print(f"[KIS] KR portfolio 조회 실패: {e}", file=sys.stderr)

        # 2. US Portfolio (TTTS3012R / VTTS3012R)
        # Check both NASD and NYSE to be sure
        for exchange in ["NASD", "NYSE"]:
            us_url = f"{self.domain}/uapi/overseas-stock/v1/trading/inquire-balance"
            us_tr_id = "VTTS3012R" if self.mock else "TTTS3012R"
            us_headers = self._get_headers(us_tr_id)
            us_params = {
                "CANO": cano, "ACNT_PRDT_CD": acnt_prdt_cd,
                "OVRS_EXCG_CD": exchange, "TR_CRCY_CD": "USD", "CTX_AREA_FK200": "", "CTX_AREA_NK200": ""
            }
            
            try:
                us_res = requests.get(us_url, headers=us_headers, params=us_params, timeout=15)
                if us_res.status_code == 200:
                    for item in us_res.json().get("output1", []):
                        qty = float(item.get("ovrs_cblc_qty", 0))
                        if qty > 0:
                            pf.append({
                                "stock_code": item.get("ovrs_pdno"),
                                "quantity": qty,
                                "current_price": float(item.get("now_pric2", 0)),
                                "average_price": float(item.get("pchs_avg_pric", 0)),
                                "pnl_percent": float(item.get("evlu_pfls_rt", 0)),
                                "market_type": "US"
                            })
            except Exception as e:
                print(f"[KIS] US portfolio ({exchange}) 조회 실패: {e}", file=sys.stderr)

        return pf

    @kis_rate_limit
    def place_order(self, code: str, qty: int, price: float, is_buy: bool, market: str = "KR") -> bool:
        """Execute buy or sell for KR or US markets."""
        cano = self.account_no.split("-")[0] if "-" in self.account_no else self.account_no[:8]
        acnt_prdt_cd = self.account_no.split("-")[1] if "-" in self.account_no else self.account_no[8:]
        
        if market.upper() == "KR":
            url = f"{self.domain}/uapi/domestic-stock/v1/trading/order-cash"
            if self.mock:
                tr_id = "VTTC0802U" if is_buy else "VTTC0801U"
            else:
                tr_id = "TTTC0802U" if is_buy else "TTTC0801U"
                
            headers = self._get_headers(tr_id)
            body = {
                "CANO": cano, "ACNT_PRDT_CD": acnt_prdt_cd,
                "PDNO": code, "ORD_DVSN": "01" if price > 0 else "02", # 01 is Limit, 02 is Market
                "ORD_QTY": str(qty), "ORD_UNPR": str(int(price))
            }
        else: # US Market
            url = f"{self.domain}/uapi/overseas-stock/v1/trading/order"
            if self.mock:
                tr_id = "VTTT1002U" if is_buy else "VTTT1001U"
            else:
                tr_id = "JTTT1002U" if is_buy else "JTTT1001U"
                
            if price <= 0:
                print("[KIS] ❌ US 주문은 지정가(Limit) 주문만 가능합니다 (price > 0).", file=sys.stderr)
                return False
                
            headers = self._get_headers(tr_id)
            body = {
                "CANO": cano, "ACNT_PRDT_CD": acnt_prdt_cd,
                "OVRS_EXCG_CD": "NASD", "PDNO": code, 
                "ORD_QTY": str(qty), "OVRS_ORD_UNPR": str(price),
                "ORD_SVR_DVSN_CD": "0", "ORD_DVSN": "00" # 00 is Limit
            }
        
        res = requests.post(url, headers=headers, json=body)
        if res.status_code == 200 and res.json().get("rt_cd") == "0":
            return True
        print(f"[KIS] ❌ 주문 오류: {res.text}", file=sys.stderr)
        return False
