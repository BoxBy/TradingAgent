import os
from datetime import datetime
import config

def plot_and_save_chart(ohlcv_df, stock_code: str, analysis_results: dict):
    """주어진 OHLCV 데이터와 기술적 분석 결과를 바탕으로 차트를 그리고 이미지 파일로 저장합니다."""
    try:
        import mplfinance as mpf
        import pandas as pd
    except ImportError:
        print(f"mplfinance not installed. Skipping chart for {stock_code}.")
        return None

    if ohlcv_df is None or len(ohlcv_df) < 20:
        print(f"Not enough data to plot chart for {stock_code}.")
        return None

    file_name = f"{stock_code}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.png"
    save_path = os.path.join(config.LOG_DIR, "charts")
    os.makedirs(save_path, exist_ok=True)
    full_path = os.path.join(save_path, file_name)

    try:
        plot_df = ohlcv_df.copy()
        addplots = []

        if analysis_results and "SMA_20" in analysis_results:
            plot_df["SMA_20"] = analysis_results["SMA_20"]
            addplots.append(mpf.make_addplot(plot_df["SMA_20"], color="blue", width=0.7))
        if analysis_results and "SMA_60" in analysis_results:
            plot_df["SMA_60"] = analysis_results["SMA_60"]
            addplots.append(mpf.make_addplot(plot_df["SMA_60"], color="green", width=0.7))

        style = mpf.make_mpf_style(base_mpf_style="yahoo", gridstyle=":")
        title = f"\n{stock_code} Technical Analysis ({datetime.now().strftime('%Y-%m-%d')})"

        mpf.plot(
            plot_df, type="candle", style=style, title=title,
            ylabel="Price", volume=True, ylabel_lower="Volume",
            addplot=addplots if addplots else None,
            figsize=(15, 7), savefig=full_path, panel_ratios=(3, 1),
        )
        print(f"Chart for {stock_code} saved to {full_path}")
        return full_path
    except Exception as e:
        print(f"Failed to plot chart for {stock_code}: {e}")
        return None
