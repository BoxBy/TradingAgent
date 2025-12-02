import time

import pandas as pd
from forex_python.converter import CurrencyRates  # ✨ forex-python 라이브러리 import
from pykis import Api, DomainInfo
from pykis.request_utility import APIRequestParameter

from .. import config
from ..utils import logger
from ..utils.rate_limiter import kis_api_rate_limiter

log = logger.get_logger(__name__)


class TradingInterface:
    """
    pykis 라이브러리를 사용하는 한국투자증권 API 인터페이스 (모의투자 계좌 통합)
    환율 조회는 forex-python 라이브러리를 사용합니다.
    """

    def __init__(self, mock_trading: bool = config.MOCK_TRADING):
        self.mock_trading = mock_trading
        self.broker = None
        self.currency_converter = CurrencyRates()  # 환율 변환기 인스턴스 생성

        try:
            key_info = {
                "appkey": (
                    config.KIS_MOCK_APP_KEY if self.mock_trading else config.KIS_APP_KEY
                ),
                "appsecret": (
                    config.KIS_MOCK_APP_SECRET
                    if self.mock_trading
                    else config.KIS_APP_SECRET
                ),
            }
            acc_no = (
                config.KIS_MOCK_ACCOUNT_NO
                if self.mock_trading
                else config.KIS_ACCOUNT_NO
            )
            if not acc_no or "-" not in acc_no:
                raise ValueError(
                    f"계좌번호 형식이 올바르지 않습니다. '{acc_no}' (하이픈 포함 필요)"
                )
            account_info = {
                "account_code": acc_no.split("-")[0],
                "product_code": acc_no.split("-")[1],
            }
            if self.mock_trading:
                domain_info = DomainInfo(kind="virtual")
                self.broker = Api(
                    key_info=key_info,
                    domain_info=domain_info,
                    account_info=account_info,
                )
                log.info("KIS API: 모의투자(pykis) 환경으로 초기화되었습니다.")
            else:
                self.broker = Api(key_info=key_info, account_info=account_info)
                log.info("KIS API: 실전투자(pykis) 환경으로 초기화되었습니다.")
        except Exception as e:
            log.error(f"Error initializing KIS API with pykis: {e}", exc_info=True)
            self.broker = None

    def _safe_float(self, value, default: float = 0.0) -> float:
        try:
            if value is None:
                return default
            if isinstance(value, (int, float)):
                return float(value)
            s = str(value).strip()
            if s == "" or s.lower() in {"nan", "null", "none", "-"}:
                return default
            return float(s.replace(",", ""))
        except Exception:
            return default

    def _safe_int(self, value, default: int = 0) -> int:
        try:
            if value is None:
                return default
            if isinstance(value, int):
                return value
            if isinstance(value, float):
                return int(value)
            s = str(value).strip()
            if s == "" or s.lower() in {"nan", "null", "none", "-"}:
                return default
            # 응답이 "1,234" 형태일 수 있으므로 콤마 제거 후 정수 변환
            return int(float(s.replace(",", "")))
        except Exception:
            return default

    def get_exchange_rate(
        self, from_currency: str = "USD", to_currency: str = "KRW"
    ) -> float:
        """✨ [최종 수정] forex-python을 이용해 실시간 환율 정보를 조회합니다."""
        try:
            rate = self.currency_converter.get_rate(from_currency, to_currency)
            log.info(
                f"Current {from_currency}/{to_currency} exchange rate from forex-python: {rate:.2f}"
            )
            return rate
        except Exception as e:
            log.error(
                f"Error fetching exchange rate from forex-python: {e}. Using default rate."
            )
            return 1300.0  # 오류 발생 시 기본 환율 반환

    def get_balance(self, market_type: str = "ALL") -> dict:
        """
        계좌 잔고 및 포트폴리오 정보를 통합 조회합니다.
        국내 잔고와 해외 잔고를 구분하여 조회하고, 최종적으로 합산된 정보를 반환합니다.
        """
        if not self.broker:
            return {}

        max_retries = 10
        retry_delay = 10

        portfolio_kr = []
        portfolio_us = []
        total_assets_krw = 0
        cash_balance = 0
        cash_kr_krw = 0  # 국내 현금 (원화)
        cash_us_krw = 0  # 해외 현금 (원화 환산)
        prev_day_assets_krw = 0
        kr_total_krw = False
        us_total_krw = False

        # --- 1. 국내 계좌 통합 조회 ---
        if market_type.upper() in ["ALL", "KR"]:
            for attempt in range(max_retries):
                try:
                    kr_balance_response_obj = self.broker._get_kr_total_balance()
                    kr_balance_response = kr_balance_response_obj.body

                    kr_stocks = kr_balance_response.get("output1", [])
                    if kr_stocks:
                        for stock in kr_stocks:
                            quantity = self._safe_int(stock.get("hldg_qty", 0))
                            if quantity > 0:
                                portfolio_kr.append(
                                    {
                                        "stock_code": stock["pdno"],
                                        "quantity": quantity,
                                        "average_price": self._safe_float(stock.get("pchs_avg_pric")),
                                        "current_price": self._safe_float(stock.get("prpr")),
                                        "pnl_percent": self._safe_float(stock.get("evlu_pfls_rt")),
                                        "market_type": "KR",
                                    }
                                )

                    kr_summary = kr_balance_response.get("output2", [])
                    if kr_summary:
                        cash_kr_krw = self._safe_int(kr_summary[0].get("dnca_tot_amt"))
                        cash_balance = cash_kr_krw
                        kr_total_krw = self._safe_int(kr_summary[0].get("nass_amt"))
                        total_assets_krw += kr_total_krw
                        prev_day_assets_krw = self._safe_int(
                            kr_summary[0].get("bfdy_tot_asst_evlu_amt")
                        )

                    log.info("국내 계좌 통합 조회 성공.")
                    break
                except Exception as e:
                    log.warning(
                        f"국내 계좌 통합 조회 실패 (시도 {attempt + 1}/{max_retries}): {e}"
                    )
                    if "초당 거래건수" in str(e) and attempt < max_retries - 1:
                        time.sleep(retry_delay)

        # --- 2. 해외 계좌 통합 조회 (두 API 조합) ---
        if market_type.upper() in ["ALL", "US"]:
            # 2-1. 해외 보유 종목 목록 조회 (pykis 내장 함수)
            for attempt in range(max_retries):
                try:
                    os_stock_list_res = self.broker._get_os_total_balance(
                        market_code="NASD"
                    )
                    if os_stock_list_res and os_stock_list_res.body:
                        os_stocks = os_stock_list_res.body.get("output1", [])
                        if os_stocks:
                            for stock in os_stocks:
                                quantity = int(self._safe_float(stock.get("ovrs_cblc_qty", 0)))
                                if quantity > 0:
                                    portfolio_us.append(
                                        {
                                            "stock_code": stock.get("ovrs_pdno"),
                                            "quantity": quantity,
                                            "average_price": self._safe_float(stock.get("pchs_avg_pric", 0)),
                                            "current_price": self._safe_float(stock.get("now_pric2", 0)),
                                            "pnl_percent": self._safe_float(stock.get("evlu_pfls_rt", 0)),
                                            "market_type": "US",
                                        }
                                    )
                                    # 해외 보유 종목 평가를 현금 잔고에 반영하지 않습니다.
                    log.info("해외 보유 종목 목록 조회 성공.")
                    break
                except Exception as e:
                    log.warning(f"해외 보유 종목 목록 조회 실패: {e}")
                    if "초당 거래건수" in str(e) and attempt < max_retries - 1:
                        time.sleep(retry_delay)

            # 2-2. 해외 총자산 정보 조회 (저수준 API)
            for attempt in range(max_retries):
                try:
                    params = {
                        "CANO": self.broker.account.account_code,
                        "ACNT_PRDT_CD": self.broker.account.product_code,
                        "WCRC_FRCR_DVSN_CD": "02",
                        "NATN_CD": "840",
                        "TR_MKET_CD": "00",
                        "INQR_DVSN_CD": "00",
                    }
                    req = APIRequestParameter(
                        "/uapi/overseas-stock/v1/trading/inquire-present-balance",
                        "VTRP6504R" if self.mock_trading else "CTRP6504R",
                        params,
                    )
                    res = self.broker._send_get_request(req)

                    # 응답 바디에서 요약(output3) 및 부가 요약(output2) 추출
                    os_summary_raw = res.body.get("output3", {}) or {}
                    os_summary_alt_raw = res.body.get("output2", {}) or {}

                    # 일부 환경에서 output2/3가 list로 반환될 수 있으므로 정규화
                    if isinstance(os_summary_raw, list):
                        os_summary = (
                            os_summary_raw[0] if os_summary_raw and isinstance(os_summary_raw[0], dict) else {}
                        )
                    else:
                        os_summary = os_summary_raw if isinstance(os_summary_raw, dict) else {}

                    if isinstance(os_summary_alt_raw, list):
                        os_summary_alt = (
                            os_summary_alt_raw[0] if os_summary_alt_raw and isinstance(os_summary_alt_raw[0], dict) else {}
                        )
                    else:
                        os_summary_alt = os_summary_alt_raw if isinstance(os_summary_alt_raw, dict) else {}

                    # 2-2-a. 해외 총자산(원화) -> 총자산 합계에만 반영
                    if os_summary:
                        us_total_krw = self._safe_float(os_summary.get("tot_asst_amt", 0))
                        total_assets_krw += us_total_krw

                    # 2-2-b. 해외 현금(예수금) -> cash_balance(KRW)에만 반영
                    # 가능한 키를 폭넓게 시도 (브로커/버전별로 키가 다를 수 있음)
                    krw_cash_candidates = [
                        "dnca_tot_amt",           # 원화 환산 예수금 합계
                        "wcrc_dnca_tot_amt",      # 원화 예수금
                        "wdrw_psbl_tot_amt",      # 전체 인출 가능 원화 합계(출금 가능 금액)
                        "frcr_use_psbl_amt",      # 외화 사용 가능 금액(원화 환산)
                    ]
                    usd_cash_candidates = [
                        "frcr_dnca_tot_amt",      # 외화(USD) 예수금 합계
                        "frcr_cblc_smtl",         # 외화 현금 잔고 합계 유사 필드명
                    ]

                    manual_krw_cash = 0.0
                    manual_usd_cash = 0.0

                    # 우선순위: output3 -> output2
                    for key in krw_cash_candidates:
                        v = self._safe_float(os_summary.get(key))
                        if v:
                            manual_krw_cash = v
                            break
                    if not manual_krw_cash:
                        for key in krw_cash_candidates:
                            v = self._safe_float(os_summary_alt.get(key))
                            if v:
                                manual_krw_cash = v
                                break

                    for key in usd_cash_candidates:
                        v = self._safe_float(os_summary.get(key))
                        if v:
                            manual_usd_cash = v
                            break
                    if not manual_usd_cash:
                        for key in usd_cash_candidates:
                            v = self._safe_float(os_summary_alt.get(key))
                            if v:
                                manual_usd_cash = v
                                break

                    # output2가 통화별 리스트인 경우, USD 행에서 출금/사용 가능 외화 금액을 추출
                    if not manual_usd_cash and isinstance(os_summary_alt_raw, list):
                        try:
                            usd_row = next((row for row in os_summary_alt_raw if isinstance(row, dict) and row.get("crcy_cd") == "USD"), None)
                            if usd_row:
                                per_currency_usd_candidates = [
                                    "frcr_dncl_amt_2",         # 외화 대체예수금?
                                    "frcr_drwg_psbl_amt_1",    # 외화 출금 가능 금액
                                    "nxdy_frcr_drwg_psbl_amt", # 익일 출금 가능 금액
                                ]
                                for key in per_currency_usd_candidates:
                                    v = self._safe_float(usd_row.get(key))
                                    if v:
                                        manual_usd_cash = v
                                        log.info(f"output2(USD)에서 '{key}'를 현금으로 인식: {manual_usd_cash}")
                                        break
                        except Exception as e:
                            log.warning(f"output2 USD 행 파싱 중 오류: {e}")

                    added_cash_krw = 0.0
                    if manual_krw_cash > 0:
                        added_cash_krw += manual_krw_cash
                    if manual_usd_cash > 0:
                        fx = self.get_exchange_rate("USD", "KRW") or 1300.0
                        added_cash_krw += manual_usd_cash * float(fx)

                    if added_cash_krw > 0:
                        cash_us_krw = int(added_cash_krw)
                        cash_balance += cash_us_krw
                        log.info(f"해외 예수금을 현금 잔고에 반영했습니다: ₩{added_cash_krw:,.0f}")
                    elif int(cash_balance) == 0 and us_total_krw:
                        # 응답에 예수금 관련 금액이 모두 0으로 제공되는 환경 대응: 총 해외자산을 현금 근사치로 사용
                        try:
                            fx = self.get_exchange_rate("USD", "KRW") or 1300.0
                            portfolio_us_value_krw = 0.0
                            for stock in portfolio_us:
                                qty = stock.get("quantity", 0) or 0
                                cur_price_usd = stock.get("current_price", 0.0) or 0.0
                                portfolio_us_value_krw += float(qty) * float(cur_price_usd) * float(fx)

                            approx_cash_krw = us_total_krw - portfolio_us_value_krw
                            if approx_cash_krw > 0:
                                cash_us_krw = int(approx_cash_krw)
                                cash_balance += cash_us_krw
                                log.info(
                                    f"해외 보유 종목 평가액을 차감한 예수금 근사치를 사용했습니다: ₩{approx_cash_krw:,.0f}"
                                )
                            else:
                                cash_us_krw = int(us_total_krw)
                                cash_balance += cash_us_krw
                                log.info("해외 예수금 근사치가 0 이하여서 총 해외자산을 현금 근사치로 사용했습니다.")
                        except Exception as e:
                            log.warning(f"해외 예수금 근사치 계산 중 오류: {e}")
                            cash_us_krw = int(us_total_krw)
                            cash_balance += cash_us_krw
                            log.info("해외 예수금 필드가 0이어서 총 해외자산을 현금 근사치로 반영했습니다.")

                    log.info("해외 총자산 정보 조회 성공.")
                    break
                except Exception as e:
                    log.warning(f"해외 총자산 정보 조회 실패: {e}")
                    if "초당 거래건수" in str(e) and attempt < max_retries - 1:
                        time.sleep(retry_delay)

        # --- 3. 최종 결과 반환 ---
        return {
            "total_assets": total_assets_krw,
            "cash_balance": cash_balance,
            "cash_kr_krw": cash_kr_krw,
            "cash_us_krw": cash_us_krw,
            "portfolio_kr": portfolio_kr,
            "portfolio_us": portfolio_us,
            "prev_day_assets": prev_day_assets_krw,
            "us_total_krw": us_total_krw,
            "kr_total_krw": kr_total_krw,
        }

    @kis_api_rate_limiter
    def fetch_price(self, stock_code: str, market: str = "US") -> float:
        if not self.broker:
            return None
        max_retries = 10
        retry_delay = 3

        last_error = None
        market_upper = market.upper()

        # KR 시장에 대해서만 pykis 현재가 API를 재시도 루프로 사용합니다.
        if market_upper != "US":
            for attempt in range(max_retries):
                try:
                    price = self.broker.get_kr_current_price(stock_code)
                    # 유효성 체크: None, 0, 음수 방지
                    if price is None or (isinstance(price, (int, float)) and price <= 0):
                        raise ValueError(f"Invalid price returned: {price}")
                    return float(price)
                except Exception as e:
                    msg = str(e)
                    last_error = msg
                    log.warning(
                        f"Error fetching price for {stock_code} (attempt {attempt+1}/{max_retries}): {msg}"
                    )
                    retriable = (
                        "초당 거래건수" in msg
                        or "http response: 500" in msg
                        or "status code: 500" in msg
                        or "Internal Server Error" in msg
                        or "could not convert string to float" in msg
                        or "Invalid price returned" in msg
                    )
                    if retriable and attempt < max_retries - 1:
                        time.sleep(retry_delay)
                    else:
                        break

        # Additional low-level fallback: call overseas price endpoint directly (US 전용)
        if market_upper == "US":
            try:
                for excd in ("NASD", "NAS"):
                    params = {
                        "AUTH": "",
                        "EXCD": excd,
                        "SYMB": stock_code.upper(),
                    }

                    # /uapi/overseas-price/v1/quotations/price-detail 호출에 대해
                    # 초당 거래건수(rate limit) 에러가 발생하면 짧게 대기 후 소규모 재시도합니다.
                    max_detail_retries = 3
                    for detail_attempt in range(max_detail_retries):
                        try:
                            req = APIRequestParameter(
                                "/uapi/overseas-price/v1/quotations/price-detail",
                                "HHDFS76200200",
                                params,
                            )
                            res = self.broker._send_get_request(req)

                            # 응답 스키마: body.output 이 단일 객체(dict) 또는 리스트일 수 있으므로 안전하게 정규화
                            try:
                                if hasattr(res, "outputs") and res.outputs:
                                    info = res.outputs[0]
                                else:
                                    output_raw = res.body.get("output", {}) if isinstance(res.body, dict) else {}
                                    if isinstance(output_raw, list):
                                        info = output_raw[0] if output_raw else {}
                                    elif isinstance(output_raw, dict):
                                        info = output_raw
                                    else:
                                        info = {}
                            except Exception:
                                info = {}

                            # 현재가(last)가 유효한 경우에만 사용하고, 그렇지 않으면 이 fallback은 건너뜁니다.
                            last_price = self._safe_float(info.get("last"))
                            if last_price and last_price > 0:
                                log.info(
                                    f"Fetched price via direct quotations/price-detail ({excd}) for {stock_code}: {last_price}"
                                )
                                return float(last_price)

                            # 유효한 가격이 없으면 이 거래소(excd)에 대해서는 더 이상 재시도하지 않고 다음 EXCD로 넘어갑니다.
                            break
                        except Exception as e:
                            msg = str(e)
                            log.warning(
                                f"Error fetching price-detail for {stock_code} on {excd} (attempt {detail_attempt+1}/{max_detail_retries}): {msg}"
                            )
                            if (
                                "초당 거래건수" in msg
                                or "http response: 500" in msg
                                or "status code: 500" in msg
                            ) and detail_attempt < max_detail_retries - 1:
                                time.sleep(retry_delay)
                                continue
                            # 그 외 오류는 상위 핸들러에서 처리
                            raise
            except Exception as e:
                log.warning(f"Direct quotations/price fallback failed for {stock_code}: {e}")

        # Fallback: 해외 시장의 경우 보유 종목 목록에서 now_pric2를 조회 (US 전용)
        if market_upper == "US":
            try:
                os_stock_list_res = self.broker._get_os_total_balance(market_code="NASD")
                if os_stock_list_res and os_stock_list_res.body:
                    os_stocks = os_stock_list_res.body.get("output1", [])
                    for stock in os_stocks or []:
                        if stock.get("ovrs_pdno") == stock_code:
                            v = self._safe_float(stock.get("now_pric2"))
                            if v and v > 0:
                                log.info(f"Fetched price via fallback now_pric2 for {stock_code}: {v}")
                                return float(v)
            except Exception as e:
                log.warning(f"Fallback now_pric2 lookup failed for {stock_code}: {e}")

        # 최종 실패: 0.0을 반환하지 않고 None 반환
        log.error(f"Failed to fetch valid price for {stock_code}. Last error: {last_error}")
        return None

    @kis_api_rate_limiter
    def fetch_historical_data(
        self,
        stock_code: str,
        market: str = "US",
        timeframe: str = "D",
        period: int = 100,
    ) -> pd.DataFrame:
        if not self.broker:
            return None
        max_retries = 10
        retry_delay = 10
        for attempt in range(max_retries):
            try:
                if market.upper() == "US":
                    log.warning(
                        f"The provided pykis version does not support overseas historical data. Skipping for {stock_code}."
                    )
                    return None
                elif market.upper() == "KR":
                    df = self.broker.get_kr_ohlcv(stock_code, time_unit=timeframe)
                    return df.tail(period)
                else:
                    raise ValueError(f"지원하지 않는 시장입니다: {market}")
            except Exception as e:
                msg = str(e)
                log.warning(f"Error fetching historical data for {stock_code}: {msg}")
                retriable = (
                    "초당 거래건수" in msg
                    or "http response: 500" in msg
                    or "status code: 500" in msg
                    or "Internal Server Error" in msg
                )
                if retriable and attempt < max_retries - 1:
                    time.sleep(retry_delay)
                else:
                    if not retriable:
                        log.error(f"Non-retriable error for {stock_code}: {msg}")
                    return None

    @kis_api_rate_limiter
    def _get_kr_stock_balance(self):
        return self.broker.get_kr_stock_balance()

    @kis_api_rate_limiter
    def _get_os_stock_balance(self):
        return self.broker.get_os_stock_balance()

    @kis_api_rate_limiter
    def _get_kr_buyable_cash(self):
        return self.broker.get_kr_buyable_cash()

    def get_portfolio(self) -> list:
        balance = self.get_balance()

        return balance.get("portfolio_kr", []) + balance.get("portfolio_us", [])

    @kis_api_rate_limiter
    def place_buy_order(
        self, stock_code: str, quantity: int, price: float = 0, market: str = "US"
    ):
        if not self.broker:
            return False  # 변경: None -> False
        try:
            log.info(f"Placing BUY order: {quantity} shares of {stock_code}")
            if market.upper() == "US":
                if price <= 0:
                    log.error("Overseas stock orders must be limit orders (price > 0).")
                    return False  # 변경: None -> False
                response = self.broker.buy_os_stock("NAS", stock_code, quantity, price)
            else:
                response = self.broker.buy_kr_stock(stock_code, quantity, int(price))
            log.info(f"BUY order response for {stock_code}: {response}")
            # ✨ 변경: 성공 시 True 반환
            return True if response else False
        except Exception as e:
            log.error(f"Error placing BUY order for {stock_code}: {e}")
            return False  # 변경: None -> False

    @kis_api_rate_limiter
    def place_sell_order(
        self, stock_code: str, quantity: int, price: float = 0, market: str = "US"
    ):
        if not self.broker:
            return False  # 변경: None -> False
        try:
            log.info(f"Placing SELL order: {quantity} shares of {stock_code}")
            if market.upper() == "US":
                if price <= 0:
                    log.error("Overseas stock orders must be limit orders (price > 0).")
                    return False  # 변경: None -> False
                response = self.broker.sell_os_stock(
                    "NASD", stock_code, quantity, price
                )
            else:
                response = self.broker.sell_kr_stock(stock_code, quantity, int(price))
            log.info(f"SELL order response for {stock_code}: {response}")
            # ✨ 변경: 성공 시 True 반환
            return True if response else False
        except Exception as e:
            log.error(f"Error placing SELL order for {stock_code}: {e}")
            return False  # 변경: None -> False

    # --- Phase 1: Fundamental Data Methods with Caching ---

    def _get_cached_data(self, key: str, ttl_seconds: int = 86400):
        """Simple memory cache getter"""
        if not hasattr(self, "_cache"):
            self._cache = {}
        
        if key in self._cache:
            data, timestamp = self._cache[key]
            if time.time() - timestamp < ttl_seconds:
                return data
            else:
                del self._cache[key]
        return None

    def _set_cached_data(self, key: str, data: any):
        """Simple memory cache setter"""
        if not hasattr(self, "_cache"):
            self._cache = {}
        self._cache[key] = (data, time.time())

    @kis_api_rate_limiter
    def get_stock_basic_info(self, stock_code: str) -> dict:
        """
        국내주식 주식기본조회 (현재가 시세2 TR 활용)
        PER, PBR, EPS, BPS, 시가총액 등을 포함한 상세 정보를 반환합니다.
        """
        if not self.broker:
            return {}
        
        # Cache check
        cache_key = f"basic_info_{stock_code}"
        cached = self._get_cached_data(cache_key, ttl_seconds=3600) # 1 hour TTL for basic info
        if cached:
            return cached

        try:

            # FHKST01010100 (주식현재가시세) - Provides PER, PBR, Market Cap
            url_path = "/uapi/domestic-stock/v1/quotations/inquire-price"
            tr_id = "FHKST01010100"
            params = {
                "FID_COND_MRKT_DIV_CODE": "J",
                "FID_INPUT_ISCD": stock_code
            }
            req = APIRequestParameter(url_path, tr_id, params)
            
            res = self.broker._send_get_request(req)
            if res and res.body:
                if isinstance(res.body, dict):
                    # Check for rate limit error in body
                    rt_cd = res.body.get("rt_cd", "")
                    msg = res.body.get("msg1", "")
                    if rt_cd != "0" and "초당 거래건수" in msg:
                        log.warning(f"Rate limit exceeded for basic info {stock_code}")
                        return {}

                data = res.body.get("output") or {}
                if data:
                    self._set_cached_data(cache_key, data)
                    return data
            
            return {}
        except Exception as e:
            log.error(f"Error fetching stock basic info for {stock_code}: {e}")
            return {}

    @kis_api_rate_limiter
    def get_investment_opinion(self, stock_code: str) -> dict:
        """
        국내주식 종목투자의견 (CTPF1604R)
        실전 투자 계좌에서만 가능.
        """
        if not self.broker or self.mock_trading:
            return {}

        cache_key = f"invest_opinion_{stock_code}"
        cached = self._get_cached_data(cache_key)
        if cached:
            return cached

        try:
            # URL and TR ID based on search results
            url_path = "/uapi/domestic-stock/v1/quotations/search-info"
            tr_id = "CTPF1604R"
            params = {
                "PDNO": stock_code,
                "PRDT_TYPE_CD": "300"
            }
            
            req = APIRequestParameter(url_path, tr_id, params)
            res = self.broker._send_get_request(req)
            
            if res and res.body and "output" in res.body:
                data = res.body["output"]
                self._set_cached_data(cache_key, data)
                return data
            return {}

        except Exception as e:
            log.warning(f"Error fetching investment opinion for {stock_code}: {e}")
            return {}

    @kis_api_rate_limiter
    def get_financial_ratios(self, stock_code: str) -> dict:
        if not self.broker:
            return {}
        
        cache_key = f"financial_ratios_{stock_code}"
        cached = self._get_cached_data(cache_key, ttl_seconds=86400)
        if cached:
            return cached

        try:
            # 국내주식 재무비율 API
            # TR ID: FHKST66430300
            url_path = "/uapi/domestic-stock/v1/finance/financial-ratio"
            tr_id = "FHKST66430300"
            params = {
                "FID_COND_MRKT_DIV_CODE": "J",
                "FID_INPUT_ISCD": stock_code,
                "FID_DIV_CLS_CODE": "1",
            }
            
            req = APIRequestParameter(url_path, tr_id, params)
            res = self.broker._send_get_request(req)
            
            if res and res.body:
                data = res.body.get("output") or {}
                if data:
                    self._set_cached_data(cache_key, data)
                    return data
            
            return {}
        except Exception as e:
            log.error(f"Error fetching financial ratios for {stock_code}: {e}")
            return {}

    @kis_api_rate_limiter
    def get_balance_sheet(self, stock_code: str) -> dict:
        if not self.broker:
            return {}
        
        cache_key = f"balance_sheet_{stock_code}"
        cached = self._get_cached_data(cache_key, ttl_seconds=86400)
        if cached:
            return cached

        try:
            # 국내주식 대차대조표 API
            # TR ID: FHKST66430100
            url_path = "/uapi/domestic-stock/v1/finance/balance-sheet"
            tr_id = "FHKST66430100"
            params = {
                "FID_COND_MRKT_DIV_CODE": "J",
                "FID_INPUT_ISCD": stock_code,
                "FID_DIV_CLS_CODE": "1",
            }
            
            req = APIRequestParameter(url_path, tr_id, params)
            res = self.broker._send_get_request(req)
            
            if res and res.body and "output" in res.body:
                data = res.body["output"]
                self._set_cached_data(cache_key, data)
                return data
            return {}
        except Exception as e:
            log.warning(f"Error fetching balance sheet for {stock_code}: {e}")
            return {}

    @kis_api_rate_limiter
    def get_income_statement(self, stock_code: str) -> dict:
        if not self.broker:
            return {}
        
        cache_key = f"income_statement_{stock_code}"
        cached = self._get_cached_data(cache_key, ttl_seconds=86400)
        if cached:
            return cached

        try:
            # 국내주식 손익계산서 API
            # TR ID: FHKST66430200
            url_path = "/uapi/domestic-stock/v1/finance/income-statement"
            tr_id = "FHKST66430200"
            params = {
                "FID_COND_MRKT_DIV_CODE": "J",
                "FID_INPUT_ISCD": stock_code,
                "FID_DIV_CLS_CODE": "1",
            }
            
            req = APIRequestParameter(url_path, tr_id, params)
            res = self.broker._send_get_request(req)
            
            if res and res.body and "output" in res.body:
                data = res.body["output"]
                self._set_cached_data(cache_key, data)
                return data
            return {}
        except Exception as e:
            log.warning(f"Error fetching income statement for {stock_code}: {e}")
            return {}



