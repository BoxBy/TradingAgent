from typing import Dict

import numpy as np
import pandas as pd
from scipy.optimize import minimize

from ..utils import logger

log = logger.get_logger(__name__)


class PortfolioOptimizer:
    """
    현대 포트폴리오 이론(MPT)을 기반으로 포트폴리오를 최적화합니다.
    """

    def __init__(self, historical_data: Dict[str, pd.DataFrame]):
        self.prices_df = self._prepare_data(historical_data)
        if self.prices_df.empty or len(self.prices_df.columns) < 2:
            raise ValueError(
                "포트폴리오 최적화를 위해서는 최소 2개 이상의 종목 데이터가 필요합니다."
            )
        self.log_returns = np.log(self.prices_df / self.prices_df.shift(1)).dropna()
        self.num_assets = len(self.prices_df.columns)
        self.asset_names = self.prices_df.columns.tolist()

    def _prepare_data(self, historical_data: Dict[str, pd.DataFrame]) -> pd.DataFrame:
        df_list = []
        for ticker, df in historical_data.items():
            if df is not None and "Close" in df.columns:
                df_list.append(df[["Close"]].rename(columns={"Close": ticker}))
        if not df_list:
            return pd.DataFrame()
        combined_df = pd.concat(df_list, axis=1)
        return combined_df.ffill().bfill()

    def _calculate_portfolio_performance(
        self, weights, returns, cov_matrix, risk_free_rate
    ):
        portfolio_return = np.sum(returns.mean() * weights) * 252
        portfolio_std = np.sqrt(
            np.dot(weights.T, np.dot(cov_matrix, weights))
        ) * np.sqrt(252)
        sharpe_ratio = (
            (portfolio_return - risk_free_rate) / portfolio_std
            if portfolio_std != 0
            else -1
        )
        return portfolio_return, portfolio_std, sharpe_ratio

    def find_optimal_weights_for_sharpe(self, risk_free_rate=0.02) -> Dict[str, float]:
        """
        샤프 지수를 최대화하는 최적의 포트폴리오 가중치를 찾습니다.
        """
        log.info("Finding optimal portfolio weights to maximize Sharpe Ratio...")
        returns = self.log_returns
        cov_matrix = returns.cov() * 252

        def neg_sharpe_ratio(weights):
            return -self._calculate_portfolio_performance(
                weights, returns, cov_matrix, risk_free_rate
            )[2]

        constraints = {"type": "eq", "fun": lambda weights: np.sum(weights) - 1}
        # 각 종목의 최소 비중 5%, 최대 비중 50%로 제한
        bounds = tuple((0.05, 0.5) for _ in range(self.num_assets))
        initial_weights = np.array([1.0 / self.num_assets] * self.num_assets)

        opt_results = minimize(
            neg_sharpe_ratio,
            initial_weights,
            method="SLSQP",
            bounds=bounds,
            constraints=constraints,
        )

        optimal_weights = {
            ticker: weight for ticker, weight in zip(self.asset_names, opt_results.x)
        }
        log.info(f"Optimal weights found: {optimal_weights}")
        return optimal_weights
