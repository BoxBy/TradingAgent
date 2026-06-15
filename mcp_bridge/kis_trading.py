import os
import sys
import requests
import time
import json
from datetime import datetime
from functools import wraps
from typing import Optional, List, Dict, Any, Union
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
            res = requests.post(url, headers=headers, json=body, timeout=10)
            
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
        
        res = requests.get(url, headers=headers, params=params, timeout=10)
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
        res = requests.get(url, headers=headers, params=params, timeout=10)
        return res.json().get("output", []) if res.status_code == 200 else []

    @kis_rate_limit
    def get_balance_sheet(self, code: str) -> list:
        """FHKST66430100"""
        url = f"{self.domain}/uapi/domestic-stock/v1/finance/balance-sheet"
        headers = self._get_headers("FHKST66430100")
        params = {"FID_COND_MRKT_DIV_CODE": "J", "FID_INPUT_ISCD": code, "FID_DIV_CLS_CODE": "0"}
        res = requests.get(url, headers=headers, params=params, timeout=10)
        return res.json().get("output", []) if res.status_code == 200 else []

    @kis_rate_limit
    def get_income_statement(self, code: str) -> list:
        """FHKST66430300"""
        url = f"{self.domain}/uapi/domestic-stock/v1/finance/income-statement"
        headers = self._get_headers("FHKST66430300")
        params = {"FID_COND_MRKT_DIV_CODE": "J", "FID_INPUT_ISCD": code, "FID_DIV_CLS_CODE": "0"}
        res = requests.get(url, headers=headers, params=params, timeout=10)
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
                    # 'prvs_rcdl_excc_amt' or 'nxdy_excc_amt' is better for "buyable" cash than 'dnca_tot_amt'
                    return float(output2[0].get("prvs_rcdl_excc_amt", output2[0].get("dnca_tot_amt", 0)))
            else:
                print(f"[KIS] get_buyable_cash HTTP {res.status_code}: {res.text}", file=sys.stderr)
        except Exception as e:
            print(f"[KIS] get_buyable_cash 실패: {e}", file=sys.stderr)
        return 0.0

    @kis_rate_limit
    def get_us_cash(self) -> float:
        """Fetch US foreign currency deposit (USD).
        output2 USD row에서 직접 조회 → 전부 0이면 output3 frcr_evlu_tota에서 역산.
        """
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

                # 1. output2 USD row에서 직접 조회
                output2 = data.get("output2", [])
                if isinstance(output2, list):
                    for row in output2:
                        if isinstance(row, dict) and row.get("crcy_cd") == "USD":
                            for key in [
                                "frcr_dncl_amt_2",       # 외화 대체예수금
                                "frcr_sll_amt_smtl",     # 외화 매도금액 합계
                                "frcr_buy_amt_smtl",     # 외화 매수금액 합계
                                "frcr_drwg_psbl_amt_1",  # 외화 출금 가능 금액
                                "nxdy_frcr_drwg_psbl_amt", # 익일 출금 가능 금액
                            ]:
                                try:
                                    val = float(row.get(key, 0))
                                    if val > 0:
                                        print(f"[KIS] get_us_cash found {key}: {val}", file=sys.stderr)
                                        return val
                                except:
                                    pass
                            break

                # 2. output2 전부 0 → output3에서 역산 (모투 API 특성)
                # 2026-06-14 확정: output2 USD row 전부 0은 KIS 모투 정상 동작.
                # frcr_evlu_tota - evlu_amt_smtl = 외화 예수금(KRW), / 환율 = USD.
                output3 = data.get("output3", {})
                if isinstance(output3, dict):
                    frcr_total_krw = float(output3.get("frcr_evlu_tota", 0))
                    holdings_krw = float(output3.get("evlu_amt_smtl", 0))
                    # frcr_evlu_tota = 예수금(USD→KRW) + 보유주식평가
                    # 예수금(KRW) = frcr_evlu_tota - holdings
                    cash_krw = frcr_total_krw - holdings_krw
                    if cash_krw > 0:
                        rate = self.get_exchange_rate()
                        if rate > 0:
                            cash_usd = cash_krw / rate
                            print(f"[KIS] get_us_cash from output3: ₩{cash_krw:,.0f} / {rate:.1f} = ${cash_usd:,.2f}", file=sys.stderr)
                            return cash_usd
            else:
                print(f"[KIS] get_us_cash HTTP {res.status_code}: {res.text}", file=sys.stderr)
        except Exception as e:
            print(f"[KIS] get_us_cash 실패: {e}", file=sys.stderr)
        return 0.0

    @kis_rate_limit
    def get_us_total_assets_krw(self) -> float:
        """Get total overseas assets (portfolio + cash + settlement) in KRW from output3.tot_asst_amt"""
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
                    # tot_asst_amt: 총자산금액 (KRW, = evlu_amt_smtl + frcr_evlu_tota)
                    # 2026-06-02: frcr_evlu_tota was ONLY cash/settlement portion (~₩237M),
                    #   NOT total assets. tot_asst_amt (~₩381M) = stocks + cash + settlement.
                    #   Verified: tot_asst_amt ≈ KIS 화면 총평가금액.
                    total = float(output3.get("tot_asst_amt", 0))

                    if total > 0:
                        print(f"[KIS] get_us_total_assets_krw: {total:,.0f} KRW", file=sys.stderr)
                        return total
            else:
                print(f"[KIS] get_us_total_assets_krw HTTP {res.status_code}: {res.text}", file=sys.stderr)
        except Exception as e:
            print(f"[KIS] get_us_total_assets_krw 실패: {e}", file=sys.stderr)
        return 0.0

    @kis_rate_limit
    def get_exchange_rate_real_domain(self) -> Optional[float]:
        """Fetches exchange rate specifically using the real domain as a fallback for Mock mode."""
        real_domain = "https://openapi.koreainvestment.com:9443"
        # We need the real app keys for this
        app_key = os.getenv("KIS_APP_KEY")
        app_secret = os.getenv("KIS_APP_SECRET")
        
        if not app_key or not app_secret:
            return None
            
        try:
            # Get real token
            token_url = f"{real_domain}/oauth2/tokenP"
            token_res = requests.post(token_url, json={
                "grant_type": "client_credentials",
                "appkey": app_key,
                "appsecret": app_secret
            }, timeout=10)
            token = token_res.json().get("access_token")
            
            if not token:
                return None
                
            # Call real API
            url = f"{real_domain}/uapi/overseas-stock/v1/quotations/inquire-daily-chartprice"
            headers = {
                "content-type": "application/json",
                "authorization": f"Bearer {token}",
                "appkey": app_key,
                "appsecret": app_secret,
                "tr_id": "FHKST03011100"
            }
            params = {
                "FID_COND_MRKT_DIV_CODE": "U", "FID_INPUT_ISCD": "FX@USDKRW",
                "FID_PERIOD_DIV_CODE": "D", "FID_ORG_ADJ_PRC": "0000000000"
            }
            res = requests.get(url, headers=headers, params=params, timeout=5)
            if res.status_code == 200:
                data = res.json()
                rate_str = data.get("output1", [{}])[0].get("stck_clpr")
                return float(rate_str) if rate_str else None
        except Exception:
            return None
        return None

    def get_exchange_rate(self) -> float:
        """Retrieve USD/KRW exchange rate. Falls back to real-domain or yfinance if fails."""
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
                rate = float(out.get("stck_clpr", 0))
                if rate > 500: return rate
        except Exception as e:
            print(f"[KIS] Standard Exchange Rate Request failed: {e}", file=sys.stderr)
            
        # Fallback 1: Try Real Domain (Specifically for Mock fails)
        try:
            real_rate = self.get_exchange_rate_real_domain()
            if real_rate and real_rate > 500:
                return real_rate
        except Exception as e:
            print(f"[KIS] Real Domain Fallback failed: {e}", file=sys.stderr)

        # Fallback 2: Try yfinance
        try:
            import yfinance as yf
            ticker = yf.Ticker("USDKRW=X")
            hist = ticker.history(period="1d")
            if not hist.empty:
                rate = hist['Close'].iloc[-1].item()
                if rate > 500: return rate
        except Exception as e:
            print(f"[KIS] yfinance Fallback failed: {e}", file=sys.stderr)
            
        return 1350.0 # Ultimate fallback

    @kis_rate_limit
    def get_portfolio(self) -> list:
        """Fetches KR (VTTC8434R) and US (VTTS3012R) portfolios and merges them.
        
        Bug fix (2025-05):
        - Deduplicates same-stock entries from split buys or cross-exchange listings
          by merging quantity, recomputing average_price and pnl_percent as weighted values.
        - KR inquiry now uses INQR_DVSN="02" to include same-day purchases.
        """
        # Use dict keyed by (stock_code, market_type) to deduplicate
        holdings = {}  # key: (stock_code, market_type) -> merged dict
        cano = self.account_no.split("-")[0] if "-" in self.account_no else self.account_no[:8]
        acnt_prdt_cd = self.account_no.split("-")[1] if "-" in self.account_no else self.account_no[8:]

        # 1. KR Portfolio - use TTTC8434R for real, VTTC8434R for mock (same as pykis)
        kr_url = f"{self.domain}/uapi/domestic-stock/v1/trading/inquire-balance"
        kr_tr_id = "VTTC8434R" if self.mock else "TTTC8434R"
        kr_headers = self._get_headers(kr_tr_id)
        kr_params = {
            "CANO": cano, "ACNT_PRDT_CD": acnt_prdt_cd,
            "AFHR_FLPR_YN": "N", "OFL_YN": "N", "INQR_DVSN": "02", "UNPR_DVSN": "01",
            "FUND_STTL_ICLD_YN": "N", "FNCG_AMT_AUTO_RDPT_YN": "N", "PRCS_DVSN": "01", "CTX_AREA_FK100": "", "CTX_AREA_NK100": ""
        }

        try:
            kr_res = requests.get(kr_url, headers=kr_headers, params=kr_params, timeout=15)
            if kr_res.status_code == 200:
                kr_data = kr_res.json()
                # Debug: log KR response for diagnostics
                with open("/tmp/kis_debug.json", "a") as f:
                    f.write(f"--- get_portfolio KR ---\n{json.dumps(kr_data, indent=2, ensure_ascii=False)}\n")
                for item in kr_data.get("output1", []):
                    qty = float(item.get("hldg_qty", 0))
                    if qty > 0:
                        code = item.get("pdno")
                        key = (code, "KR")
                        avg_price = float(item.get("pchs_avg_pric", 0))
                        cur_price = float(item.get("prpr", 0))
                        pnl_rt = float(item.get("evlu_pfls_rt", 0))
                        if key in holdings:
                            # Merge: weighted average price, sum quantities
                            existing = holdings[key]
                            total_qty = existing["quantity"] + qty
                            existing["average_price"] = (
                                (existing["average_price"] * existing["quantity"] + avg_price * qty) / total_qty
                                if total_qty > 0 else existing["average_price"]
                            )
                            existing["quantity"] = total_qty
                            # Use current price from latest entry; pnl will be recomputed
                            existing["current_price"] = cur_price
                        else:
                            holdings[key] = {
                                "stock_code": code,
                                "quantity": qty,
                                "current_price": cur_price,
                                "average_price": avg_price,
                                "pnl_percent": pnl_rt,
                                "market_type": "KR"
                            }
            else:
                print(f"[KIS] KR portfolio HTTP {kr_res.status_code}: {kr_res.text[:200]}", file=sys.stderr)
        except Exception as e:
            print(f"[KIS] KR portfolio 조회 실패: {e}", file=sys.stderr)

        # 2. US Portfolio (TTTS3012R / VTTS3012R)
        # Check both NASD and NYSE — but skip cross-listing duplicates.
        # KIS mock API returns the same position in both exchanges (e.g., SLB in NASD+NYSE).
        # Real stocks trade on one exchange; if key already exists, it's a duplicate.
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
                            code = item.get("ovrs_pdno")
                            key = (code, "US")
                            avg_price = float(item.get("pchs_avg_pric", 0))
                            cur_price = float(item.get("now_pric2", 0))
                            pnl_rt = float(item.get("evlu_pfls_rt", 0))
                            if key in holdings:
                                # Skip cross-listing duplicates (same stock in NASD+NYSE)
                                # Real stocks trade on one exchange — duplicate means KIS API quirk
                                pass
                            else:
                                holdings[key] = {
                                    "stock_code": code,
                                    "quantity": qty,
                                    "current_price": cur_price,
                                    "average_price": avg_price,
                                    "pnl_percent": pnl_rt,
                                    "market_type": "US",
                                    "ovrs_excg_cd": exchange  # NASD or NYSE
                                }
            except Exception as e:
                print(f"[KIS] US portfolio ({exchange}) 조회 실패: {e}", file=sys.stderr)

        # Recompute pnl_percent for merged entries using weighted average price
        for h in holdings.values():
            if h["average_price"] > 0 and h["current_price"] > 0:
                h["pnl_percent"] = ((h["current_price"] - h["average_price"]) / h["average_price"]) * 100

        return list(holdings.values())

    @kis_rate_limit
    def _kr_tick_size(self, price: float) -> int:
        """Return the tick size (호가단위) for a given KR stock price."""
        p = abs(int(price))
        if p < 2000: return 1
        if p < 5000: return 5
        if p < 20000: return 10
        if p < 50000: return 25
        if p < 100000: return 50
        if p < 150000: return 100
        if p < 200000: return 500
        if p < 500000: return 1000
        # 500,000+ : varies, but 5000 is a safe conservative tick
        return 5000

    def _kr_round_to_tick(self, price: float, round_down: bool = True) -> int:
        """Round a KR stock price to the nearest valid tick."""
        tick = self._kr_tick_size(price)
        p = int(price)
        if round_down:
            return (p // tick) * tick
        return ((p + tick - 1) // tick) * tick

    # Known NYSE-listed tickers (commonly traded by TA)
    _NYSE_TICKERS = frozenset({
        "XOM", "PG", "JNJ", "JPM", "BAC", "WMT", "KO", "PEP", "HD", "MCD",
        "V", "MA", "DIS", "NKE", "CVX", "PFE", "MRK", "T", "VZ", "ORCL",
        "CHD", "BRK", "ABBV", "LLY", "TMO", "UNH", "CRM",
        "WFC", "MS", "GS", "USB", "MET", "PRU", "RT", "LMT", "BA", "CAT",
        "GE", "HON", "MMM", "AXP", "SPGI", "BK", "SCHW", "TGT", "LOW",
        "COST", "SBUX", "MNST", "CL", "KMB", "MDT", "ANTM", "DOW", "IBM",
    })

    def _resolve_us_exchange(self, code: str) -> str:
        """Resolve OVRS_EXCG_CD for a US stock code.

        Order: (1) Check current portfolio for held positions
               (2) Check known NYSE ticker set
               (3) Default to NASD (most tech stocks)
        """
        code_u = code.upper()

        # 1. Check portfolio data (works for sells — we know where we hold it)
        try:
            cache_path = os.path.join(
                os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                "logs", "cached_balance.json"
            )
            if os.path.exists(cache_path):
                with open(cache_path, "r") as _cf:
                    _cb = json.load(_cf)
                for _item in _cb.get("last_get_portfolio", []):
                    if _item.get("stock_code", "").upper() == code_u:
                        excg = _item.get("ovrs_excg_cd")
                        if excg:
                            return excg
        except Exception:
            pass

        # 2. Known NYSE tickers
        if code_u in self._NYSE_TICKERS:
            return "NYSE"

        # 3. Default to NASDAQ
        return "NASD"

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
            # ORD_DVSN: 00 (Limit), 01 (Market)
            # 모의투자(VTS)에서는 시장가(01)가 에러나는 경우가 많아 지정가(00) 우선 사용
            ord_dvsn = "00" if price > 0 else "01"
            if self.mock and ord_dvsn == "01":
                # Mock mode: market orders not supported. Resolve price from cache
                # and use limit order instead (same pattern as US orders below).
                _resolved_price = 0
                try:
                    import json as _json, os as _os
                    _cache_path = _os.path.join(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))), "logs", "cached_balance.json")
                    if _os.path.exists(_cache_path):
                        with open(_cache_path, "r") as _cf:
                            _cb = _json.load(_cf)
                        for _item in _cb.get("last_get_portfolio", []):
                            if _item.get("stock_code", "").upper() == code.upper():
                                _resolved_price = _item.get("current_price", 0)
                                if _resolved_price and _resolved_price > 0:
                                    price = float(_resolved_price)
                                    # Round to valid tick size for KR market
                                    if not is_buy:
                                        # For SELL: round down 1 tick for faster fill
                                        price = self._kr_round_to_tick(price * 0.998, round_down=True)
                                        if price <= 0:
                                            price = self._kr_round_to_tick(_resolved_price)
                                    else:
                                        price = self._kr_round_to_tick(price)
                                    print(f"[KIS] ⚡ Auto-resolved KR {code} price from cache: ₩{price:,.0f}", file=sys.stderr)
                                    break
                except Exception as _ae:
                    print(f"[KIS] ⚠️ KR auto-price lookup failed: {_ae}", file=sys.stderr)
                if price > 0:
                    ord_dvsn = "00"
                else:
                    # Fallback: try market order anyway (will likely fail in mock)
                    ord_dvsn = "00"
                
            body = {
                "CANO": cano, "ACNT_PRDT_CD": acnt_prdt_cd,
                "PDNO": code, "ORD_DVSN": ord_dvsn,
                "ORD_QTY": str(qty), "ORD_UNPR": str(int(price)) if ord_dvsn == "00" else "0"
            }
        else: # US Market
            url = f"{self.domain}/uapi/overseas-stock/v1/trading/order"
            if self.mock:
                tr_id = "VTTT1002U" if is_buy else "VTTT1001U"  # VTTT1001U for mock sell (VTTT1006U blocked by mock server)
            else:
                # Official KIS TR_ID: TTTT1002U (US buy), TTTT1006U (US sell)
                tr_id = "TTTT1002U" if is_buy else "TTTT1006U"

            # Resolve exchange code (NASDAQ vs NYSE) for the stock
            ovrs_excg_cd = self._resolve_us_exchange(code)
                
            if price <= 0:
                # Attempt to auto-resolve price from cached_balance.json before rejecting
                try:
                    import json as _json, os as _os
                    _cache_path = _os.path.join(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))), "logs", "cached_balance.json")
                    if _os.path.exists(_cache_path):
                        with open(_cache_path, "r") as _cf:
                            _cb = _json.load(_cf)
                        for _item in _cb.get("last_get_portfolio", []):
                            if _item.get("stock_code", "").upper() == code.upper():
                                _cached_price = _item.get("current_price", 0)
                                if _cached_price and _cached_price > 0:
                                    price = float(_cached_price)
                                    # For SELL orders, undercut slightly for faster fill
                                    if not is_buy:
                                        price = round(price * 0.995, 2)
                                    print(f"[KIS] ⚡ Auto-resolved {code} price from cache: ${price:.2f}", file=sys.stderr)
                                    break
                except Exception as _ae:
                    print(f"[KIS] ⚠️ Auto-price lookup failed: {_ae}", file=sys.stderr)
                
                if price <= 0:
                    print("[KIS] ❌ US 주문은 지정가(Limit) 주문만 가능합니다 (price > 0).", file=sys.stderr)
                    return False
                
            headers = self._get_headers(tr_id)
            body = {
                "CANO": cano,
                "ACNT_PRDT_CD": acnt_prdt_cd,
                "OVRS_EXCG_CD": ovrs_excg_cd,
                "PDNO": code,
                "ORD_QTY": str(qty),
                "OVRS_ORD_UNPR": str(price),
                "CTAC_TLNO": "",
                "MGCO_APTM_ODNO": "",
                "SLL_TYPE": "" if is_buy else "00",  # 매도 시 필수 (00=일반매도)
                "ORD_SVR_DVSN_CD": "0",
                "ORD_DVSN": "00",  # 00: 지정가 (모의투자는 지정가만 가능)
            }
        
        # Retry on rate limit errors (EGW00201) — up to 3 attempts with backoff
        max_retries = 3
        for _attempt in range(max_retries):
            res = requests.post(url, headers=headers, json=body, timeout=10)
            if res.status_code == 200 and res.json().get("rt_cd") == "0":
                return True
            
            # Check for rate limit error
            try:
                _res_json = res.json() if res.status_code == 200 else {}
                _msg_cd = _res_json.get("msg_cd", "")
                if _msg_cd == "EGW00201" and _attempt < max_retries - 1:
                    _wait = 2.0 * (_attempt + 1)  # 2s, 4s, 6s
                    print(f"[KIS] ⏳ Rate limited (EGW00201) on attempt {_attempt+1}/{max_retries} for {code}. Waiting {_wait:.1f}s...", file=sys.stderr)
                    time.sleep(_wait)
                    continue
            except Exception:
                pass
            break  # Not a rate limit error or final attempt — proceed to error handling
        
        # --- Circuit Breaker: Flag persistent KIS mock trading rejections ---
        try:
            res_json = res.json() if res.status_code == 200 else {}
            err_msg = res_json.get("msg1", "") or res.text
            if "모의투자에서는 해당업무가 제공되지 않습니다" in err_msg or "제공되지 않습니다" in err_msg:
                flag_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "logs", "kis_execution_blocked.flag")
                with open(flag_path, "w") as _f:
                    _f.write(json.dumps({
                        "timestamp": datetime.now().isoformat(),
                        "error": err_msg,
                        "market": market,
                        "code": code,
                        "action": "BUY" if is_buy else "SELL"
                    }))
                print(f"[KIS] 🚫 Circuit breaker flag written: mock trading does not support this operation.", file=sys.stderr)
            # --- Phantom Position Detection: "잔고내역이 없습니다" ---
            # KIS mock returns this when the stock shows in inquire-balance but can't actually be sold.
            # Write a per-stock flag so the emergency SL loop can detect and clean up.
            # BUG FIX (2026-05-18): Before creating phantom flag, verify actual broker balance.
            # "잔고내역이 없습니다" can also occur due to qty mismatch (selling more than held),
            # which is NOT a phantom position — it's a stale local qty issue.
            if "잔고내역이 없습니다" in err_msg and not is_buy:
                # Verify: check actual broker balance for this stock
                _actual_qty = 0
                try:
                    _broker_pf = self.get_portfolio()
                    for _bp in _broker_pf:
                        if _bp.get("stock_code", "").upper() == code.upper():
                            _actual_qty = int(_bp.get("quantity", 0))
                            break
                except Exception:
                    pass  # If verification fails, fall through to create flag

                if _actual_qty > 0 and _actual_qty < qty:
                    # Position EXISTS but qty mismatch — retry with correct qty
                    print(f"[KIS] ⚠️ Qty mismatch for {code}: tried {qty}, actual {_actual_qty}. Retrying with correct qty.", file=sys.stderr)
                    time.sleep(2.0)  # Cool down before retry to avoid rate limit
                    return self.place_order(code, _actual_qty, price, is_buy, market)
                elif _actual_qty >= qty:
                    # Position exists with sufficient qty — this is a temporary KIS API error, NOT phantom
                    print(f"[KIS] ⚠️ Sell failed but position exists ({code}: {_actual_qty} shares). Not marking as phantom.", file=sys.stderr)
                    # Remove any existing phantom flag since position is real
                    logs_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "logs")
                    phantom_flag = os.path.join(logs_dir, f"phantom_{code.upper()}.flag")
                    if os.path.exists(phantom_flag):
                        os.remove(phantom_flag)
                        print(f"[KIS] Removed stale phantom flag for {code}", file=sys.stderr)
                else:
                    # Truly no position at broker — this IS a phantom position
                    logs_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "logs")
                    phantom_flag = os.path.join(logs_dir, f"phantom_{code.upper()}.flag")
                    with open(phantom_flag, "w") as _pf:
                        _pf.write(json.dumps({
                            "timestamp": datetime.now().isoformat(),
                            "code": code,
                            "market": market,
                            "qty": qty,
                            "actual_qty": _actual_qty,
                            "error": err_msg,
                            "msg_cd": res_json.get("msg_cd", "")
                        }))
                    print(f"[KIS] 👻 Phantom position flag written: {code} (unsellable - no balance record)", file=sys.stderr)
        except Exception:
            pass
        
        print(f"[KIS] ❌ 주문 오류: {res.text}", file=sys.stderr)
        return False
