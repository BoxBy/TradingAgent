import json
import os
from datetime import datetime, timedelta
from typing import Dict, List


from .. import config
from ..data_providers import TradingInterface
from ..services import RAGManager  # ✨ RAGManager 추가
from ..utils import logger
from ..utils.logger import TradeLogger

log = logger.get_logger(__name__)


class TradeMonitor:
    STATE_FILE = os.path.join(config.LOG_DIR, "active_trades.json")

    def __init__(
        self,
        kis_wrapper: TradingInterface,
        logger: TradeLogger,
        rag_manager: RAGManager,
    ):
        self.kis_wrapper = kis_wrapper
        self.logger = logger
        self.rag_manager = rag_manager  # RAG 매니저 저장
        self.active_trades = self._load_state()

    def _load_state(self) -> dict:
        if os.path.exists(self.STATE_FILE):
            try:
                with open(self.STATE_FILE, "r") as f:
                    return json.load(f)
            except (json.JSONDecodeError, FileNotFoundError):
                return {}
        return {}

    def _save_state(self):
        """
        데이터 유실을 방지하기 위해 임시 파일에 먼저 저장하고, 성공 시 원본 파일로 교체합니다.
        """
        temp_file = self.STATE_FILE + ".tmp"
        try:
            with open(temp_file, "w") as f:
                json.dump(self.active_trades, f, indent=4)
            os.replace(temp_file, self.STATE_FILE)
        except Exception as e:
            log.error(f"Failed to save trade state: {e}")

    def sync_with_account(self, portfolio: List[Dict]):
        """
        프로그램 시작 시, 실제 계좌 포트폴리오와 active_trades.json을 동기화합니다.
        """
        log.info("Syncing trade monitor state with live account portfolio...")
        if not portfolio:
            log.warning("Portfolio is empty. Clearing all active trades state.")
            self.active_trades.clear()
            self._save_state()
            return

        portfolio_symbols = {item["stock_code"] for item in portfolio}
        active_trade_symbols = set(self.active_trades.keys())

        # 1. active_trades에만 있는 종목 삭제
        for stock_code in list(self.active_trades.keys()):
            if stock_code not in portfolio_symbols:
                log.warning(
                    f"'{stock_code}' is in active_trades.json but not in portfolio. Removing from state."
                )
                del self.active_trades[stock_code]

        # 2. 실제 계좌에만 있는 종목을 기본값으로 신규 등록
        for stock_info in portfolio:
            stock_code = stock_info["stock_code"]
            if stock_code not in active_trade_symbols:
                log.warning(
                    f"'{stock_code}' is in portfolio but not in active_trades.json. Registering with default values."
                )

                # ✨ RAG DB에서 가장 최근 거래 기록을 조회합니다.
                restored_trade_info = self.rag_manager.retrieve_latest_trade_log(
                    stock_code
                )

                if restored_trade_info:
                    log.info(
                        f"Successfully restored trade info for '{stock_code}' from RAG DB."
                    )
                    # 복원된 정보로 active_trades에 등록합니다.
                    # 단, 수량과 평균 단가는 현재 계좌의 최신 정보로 덮어씁니다.
                    restored_trade_info["quantity"] = stock_info.get("quantity", 0)
                    restored_trade_info["purchase_price"] = stock_info.get(
                        "average_price", 0
                    )
                    self.active_trades[stock_code] = restored_trade_info
                else:
                    # RAG에도 기록이 없으면, 기존처럼 기본값으로 등록합니다.
                    log.warning(
                        f"No trade log found in RAG DB for '{stock_code}'. Registering with default values."
                    )
                    self.register_trade(
                        stock_code=stock_code,
                        purchase_price=stock_info.get("average_price", 0),
                        quantity=stock_info.get("quantity", 0),
                        target_gain_percentage=config.USER_RULES.get(
                            "target_profit_percent_per_trade"
                        ),
                        stop_loss_value=config.USER_RULES.get(
                            "max_loss_percent_per_trade"
                        ),
                        sell_deadline_date=(
                            datetime.now() + timedelta(days=7)
                        ).strftime("%Y-%m-%d"),
                        reasoning="[Auto-sync] Registered from live portfolio. Original data not found.",
                        market_type=stock_info.get("market_type", "KR"),
                    )

        self._save_state()
        log.info("Account sync complete.")

    def register_trade(
        self,
        stock_code: str,
        purchase_price: float,
        target_gain_percentage: float,
        sell_deadline_date: str,
        quantity: int,
        reasoning: str,
        market_type: str,
        stop_loss_type: str = "fixed",
        stop_loss_value: float = 10.0,
    ):
        if stock_code in self.active_trades:
            print(f"Trade for {stock_code} is already active. Skipping.")
            return

        costs = config.TRANSACTION_COSTS.get(
            market_type.upper(), config.TRANSACTION_COSTS["KR"]
        )

        buy_cost_ratio = 1 + (costs["BUY_FEE"] / 100)
        sell_cost_ratio = 1 - ((costs["SELL_FEE"] + costs["SELL_TAX"]) / 100)

        break_even_price = (purchase_price * buy_cost_ratio) / sell_cost_ratio
        target_price = break_even_price * (1 + target_gain_percentage / 100)

        trade_info = {
            "purchase_price": purchase_price,
            "target_price": target_price,
            "sell_deadline_date": sell_deadline_date,
            "status": "active",
            "quantity": quantity,
            "reasoning": reasoning,  # ✨ 2. 전달받은 reasoning을 trade_info에 저장
            "stop_loss_type": stop_loss_type,
            "stop_loss_value": stop_loss_value,
            "market_type": market_type,
        }

        if stop_loss_type == "fixed":
            trade_info["stop_loss_price"] = purchase_price * (1 - stop_loss_value / 100)
        elif stop_loss_type == "trailing":
            trade_info["stop_loss_price"] = purchase_price * (1 - stop_loss_value / 100)
            trade_info["trailing_peak_price"] = purchase_price

        self.active_trades[stock_code] = trade_info
        self._save_state()

        # ✨ 거래 등록 시 RAG DB에도 로그를 저장합니다.
        self.rag_manager.add_trade_log_to_db(stock_code, trade_info)

        print(
            f"New trade registered for {stock_code}. Target: {target_price:.2f}, Stop-Loss: {stop_loss_type}"
        )

    def check_and_execute_sells(self):
        if not self.active_trades:
            return

        print(".", end="", flush=True)
        portfolio = self.kis_wrapper.get_portfolio()
        if not portfolio:
            if self.active_trades:
                print("Portfolio is empty.")
                self._save_state()
            return

        portfolio_stocks = {item["stock_code"]: item for item in portfolio}

        for stock_code, trade_info in list(self.active_trades.items()):
            if stock_code not in portfolio_stocks:
                print(f"{stock_code} not in portfolio, removing from active trades.")
                del self.active_trades[stock_code]
                continue

            current_stock_info = portfolio_stocks[stock_code]
            current_price = current_stock_info["current_price"]
            reason = None

            if current_price >= trade_info["target_price"]:
                reason = f"Target price reached ({current_price:.2f} >= {trade_info['target_price']:.2f})"
            elif (
                datetime.now().strftime("%Y-%m-%d") >= trade_info["sell_deadline_date"]
            ):
                reason = f"Sell deadline reached ({trade_info['sell_deadline_date']})"
            else:
                stop_loss_type = trade_info.get("stop_loss_type", "fixed")
                if stop_loss_type == "fixed":
                    stop_price = trade_info.get("stop_loss_price", 0)
                    if current_price <= stop_price:
                        reason = f"Fixed stop-loss triggered ({current_price:.2f} <= {stop_price:.2f})"
                elif stop_loss_type == "trailing":
                    if current_price > trade_info.get("trailing_peak_price", 0):
                        trade_info["trailing_peak_price"] = current_price
                        trade_info["stop_loss_price"] = current_price * (
                            1 - trade_info.get("stop_loss_value", 10.0) / 100
                        )
                    stop_price = trade_info.get("stop_loss_price", 0)
                    if current_price <= stop_price:
                        reason = f"Trailing-stop triggered ({current_price:.2f} <= {stop_price:.2f})"
                elif stop_loss_type == "atr":
                    hist_data = self.kis_wrapper.fetch_historical_data(
                        stock_code, market="US", period=30
                    )
                    if hist_data is not None and not hist_data.empty:
                        hist_data.ta.atr(length=14, append=True)
                        current_atr = hist_data["ATRr_14"].iloc[-1]
                        stop_price = current_price - (
                            current_atr * trade_info.get("stop_loss_value", 2.0)
                        )
                        if current_price <= stop_price:
                            reason = f"ATR stop-loss triggered ({current_price:.2f} <= {stop_price:.2f})"

            if reason:
                print(f"\nSell condition met for {stock_code}: {reason}")
                quantity_to_sell = current_stock_info["quantity"]
                self.kis_wrapper.place_sell_order(
                    stock_code, quantity_to_sell, price=current_price, market="US"
                )
                self.logger.log_trade(
                    stock_code, "SELL", quantity_to_sell, current_price, reason
                )

                pnl_percent = (current_price / trade_info["purchase_price"] - 1) * 100
                if pnl_percent > 0:
                    insight = f"Success Insight for {stock_code}: Achieved {pnl_percent:.2f}% profit. Reason for sell: {reason}. The initial buy decision was likely correct."
                else:
                    insight = f"Failure Insight for {stock_code}: Incurred {pnl_percent:.2f}% loss. Reason for sell: {reason}. The initial buy decision should be reviewed. Was a key risk missed?"
                self.rag_manager.add_trading_insight(stock_code, insight)
                del self.active_trades[stock_code]

        self._save_state()

    def update_trade_parameters(self, stock_code: str, new_params: dict):
        """
        기존에 active_trades에 있는 종목의 거래 전략(목표수익률, 손절률 등)을 수정합니다.
        """
        if stock_code not in self.active_trades:
            log.warning(
                f"Cannot update parameters for {stock_code} as it is not in active_trades."
            )
            return

        trade_info = self.active_trades[stock_code]
        is_updated = False

        new_target_gain = new_params.get("new_target_gain_percentage")
        new_stop_loss_value = new_params.get("new_stop_loss_value")

        if new_target_gain is not None:
            is_updated = True
            purchase_price = trade_info["purchase_price"]
            # trade_info에 market_type이 저장되어 있어야 합니다. register_trade에서 꼭 저장해주세요.
            market_type = trade_info.get("market_type", "US")
            costs = config.TRANSACTION_COSTS.get(
                market_type.upper(), config.TRANSACTION_COSTS["KR"]
            )
            buy_cost_ratio = 1 + (costs["BUY_FEE"] / 100)
            sell_cost_ratio = 1 - ((costs["SELL_FEE"] + costs["SELL_TAX"]) / 100)
            break_even_price = (purchase_price * buy_cost_ratio) / sell_cost_ratio

            trade_info["target_price"] = break_even_price * (
                1 + float(new_target_gain) / 100
            )
            log.info(
                f"New target price for {stock_code} set to: {trade_info['target_price']:.2f}"
            )

        if new_stop_loss_value is not None:
            is_updated = True
            trade_info["stop_loss_value"] = float(new_stop_loss_value)
            if trade_info["stop_loss_type"] == "fixed":
                trade_info["stop_loss_price"] = trade_info["purchase_price"] * (
                    1 - trade_info["stop_loss_value"] / 100
                )
            elif trade_info["stop_loss_type"] == "trailing":
                trade_info["stop_loss_price"] = trade_info.get(
                    "trailing_peak_price", trade_info["purchase_price"]
                ) * (1 - trade_info["stop_loss_value"] / 100)
            log.info(
                f"New stop loss for {stock_code} set to: {trade_info['stop_loss_value']}%"
            )

        if is_updated:
            self.active_trades[stock_code] = trade_info
            self._save_state()
            log.info(f"Successfully updated trade parameters for {stock_code}.")
