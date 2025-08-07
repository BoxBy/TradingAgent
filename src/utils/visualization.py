import pandas as pd
import mplfinance as mpf
import os
from datetime import datetime

# 내부 모듈 import 경로 수정
from .. import config

def plot_and_save_chart(ohlcv_df: pd.DataFrame, stock_code: str, analysis_results: dict):
    """
    주어진 OHLCV 데이터와 기술적 분석 결과를 바탕으로 차트를 그리고 이미지 파일로 저장합니다.
    """
    if ohlcv_df is None or len(ohlcv_df) < 20: # 최소 20일 데이터 필요
        print(f"Not enough data to plot chart for {stock_code}.")
        return None

    # 파일명 및 저장 경로 설정
    file_name = f"{stock_code}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.png"
    save_path = os.path.join(config.LOG_DIR, "charts")
    os.makedirs(save_path, exist_ok=True)
    full_path = os.path.join(save_path, file_name)

    try:
        # 차트에 그릴 데이터를 복사하여 준비
        plot_df = ohlcv_df.copy()
        
        # 분석 결과에서 이동평균선과 볼린저밴드 데이터를 DataFrame에 추가
        # 기술적 분석 결과가 없을 수도 있으므로 .get()으로 안전하게 접근
        if analysis_results and 'SMA_20' in analysis_results:
            plot_df['SMA_20'] = analysis_results.get('SMA_20')
        if analysis_results and 'SMA_60' in analysis_results:
            plot_df['SMA_60'] = analysis_results.get('SMA_60')
        if analysis_results and 'Bollinger_Upper' in analysis_results:
            plot_df['BBU_20_2.0'] = analysis_results.get('Bollinger_Upper')
            plot_df['BBL_20_2.0'] = analysis_results.get('Bollinger_Lower')

        # 추가 플롯(addplot) 설정
        addplots = []
        if 'SMA_20' in plot_df:
            addplots.append(mpf.make_addplot(plot_df['SMA_20'], color='blue', width=0.7))
        if 'SMA_60' in plot_df:
            addplots.append(mpf.make_addplot(plot_df['SMA_60'], color='green', width=0.7))
        if 'BBU_20_2.0' in plot_df:
            # fill_between을 사용하여 볼린저 밴드 영역 채우기
            addplots.append(mpf.make_addplot(plot_df[['BBU_20_2.0', 'BBL_20_2.0']], color='gray', alpha=0.1, fill_between=True))

        # 차트 스타일 및 제목 설정
        style = mpf.make_mpf_style(base_mpf_style='yahoo', gridstyle=':')
        title = f"\n{stock_code} Technical Analysis ({datetime.now().strftime('%Y-%m-%d')})"
        
        # 차트 생성 및 저장
        mpf.plot(
            plot_df,
            type='candle',
            style=style,
            title=title,
            ylabel='Price ($)',
            volume=True,
            ylabel_lower='Volume',
            addplot=addplots,
            figsize=(15, 7),
            savefig=full_path, # 파일로 저장
            panel_ratios=(3, 1) # 가격 차트와 거래량 차트 비율
        )
        print(f"Chart for {stock_code} saved to {full_path}")
        return full_path
    except Exception as e:
        print(f"Failed to plot chart for {stock_code}: {e}")
        return None