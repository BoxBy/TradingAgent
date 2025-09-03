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
        cash_balance_krw = 0
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
                            if int(stock.get("hldg_qty", 0)) > 0:
                                portfolio_kr.append(
                                    {
                                        "stock_code": stock["pdno"],
                                        "quantity": int(stock["hldg_qty"]),
                                        "average_price": float(stock["pchs_avg_pric"]),
                                        "current_price": float(stock["prpr"]),
                                        "pnl_percent": float(stock["evlu_pfls_rt"]),
                                        "market_type": "KR",
                                    }
                                )

                    kr_summary = kr_balance_response.get("output2", [])
                    if kr_summary:
                        cash_balance_krw = int(kr_summary[0]["dnca_tot_amt"])
                        kr_total_krw = int(kr_summary[0]["nass_amt"])
                        total_assets_krw += kr_total_krw
                        prev_day_assets_krw = int(
                            kr_summary[0]["bfdy_tot_asst_evlu_amt"]
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
                                quantity = int(float(stock.get("ovrs_cblc_qty", 0)))
                                if quantity > 0:
                                    portfolio_us.append(
                                        {
                                            "stock_code": stock.get("ovrs_pdno"),
                                            "quantity": quantity,
                                            "average_price": float(
                                                stock.get("pchs_avg_pric", 0)
                                            ),
                                            "current_price": float(
                                                stock.get("now_pric2", 0)
                                            ),
                                            "pnl_percent": float(
                                                stock.get("evlu_pfls_rt", 0)
                                            ),
                                            "market_type": "US",
                                        }
                                    )
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
                    os_summary = res.body.get("output3", {})
                    if os_summary:
                        us_total_krw = float(os_summary.get("tot_asst_amt", 0))
                        # market_type이 'US'일 경우, 국내 자산이 없으므로 해외 자산을 총자산으로 설정
                        total_assets_krw += us_total_krw
                    log.info("해외 총자산 정보 조회 성공.")
                    break
                except Exception as e:
                    log.warning(f"해외 총자산 정보 조회 실패: {e}")
                    if "초당 거래건수" in str(e) and attempt < max_retries - 1:
                        time.sleep(retry_delay)

        # --- 3. 최종 결과 반환 ---
        return {
            "total_assets": total_assets_krw,
            "cash_balance": cash_balance_krw,
            "portfolio_kr": portfolio_kr,
            "portfolio_us": portfolio_us,
            "prev_day_assets": prev_day_assets_krw,
            "us_total_krw": us_total_krw,
            "kr_total_krw": kr_total_krw,
        }

    @kis_api_rate_limiter
    def fetch_price(self, stock_code: str, market: str = "US") -> float:
        if not self.broker:
            return 0.0
        try:
            if market.upper() == "US":
                return self.broker.get_os_current_price(stock_code, "NAS")
            else:
                return self.broker.get_kr_current_price(stock_code)
        except Exception as e:
            log.error(f"Error fetching price for {stock_code}: {e}")
            return 0.0

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
            log.error(f"Error fetching historical data for {stock_code}: {e}")
            return None

    @kis_api_rate_limiter
    def _get_kr_stock_balance(self):
        return self.broker.get_kr_stock_balance()

    @kis_api_rate_limiter
    def _get_os_stock_balance(self):
        # 이 함수는 이제 실전 투자 시에만 호출됩니다.
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
