import os
from datetime import datetime

from .. import config

def generate_daily_report(portfolio_status: dict, trades_log: list, analysis_summaries: dict):
    """
    하루의 거래 내역과 분석 결과를 요약하여 텍스트 리포트를 생성하고 저장합니다.
    """
    report = []
    today_str = datetime.now().strftime('%Y-%m-%d')
    report.append(f"====== Daily Trading Report: {today_str} ======")
    report.append("\n--- 1. Portfolio Status ---")
    
    if portfolio_status and 'total_assets' in portfolio_status:
        report.append(f"Total Assets: {portfolio_status.get('total_assets', 0):,.0f} KRW")
        report.append(f"Cash Balance: {portfolio_status.get('cash_balance', 0):,.0f} KRW")
        if portfolio_status.get('portfolio'):
            for stock in portfolio_status.get('portfolio'):
                report.append(
                    f"  - {stock['stock_code']}: {stock['quantity']} shares, "
                    f"Avg Price: {stock.get('average_price', 0):,.2f}, "
                    f"Current Price: {stock.get('current_price', 0):,.2f}, "
                    f"P&L: {stock.get('pnl_percent', 0)}%"
                )
        else:
            report.append("  - No stocks in portfolio.")
    else:
        report.append("  - Could not retrieve portfolio status.")

    report.append("\n--- 2. Today's Trades ---")
    if trades_log:
        for trade in trades_log:
            report.append(
                f"  - [{trade['action']}] {trade['stock_code']}: {trade['quantity']} shares @ ${trade['price']:,.2f}"
            )
            report.append(f"    Reasoning: {trade.get('reasoning', 'N/A')}")
    else:
        report.append("  - No trades were executed today.")
        
    report.append("\n--- 3. Analysis Summaries ---")
    if analysis_summaries:
        for stock_code, summary in analysis_summaries.items():
            report.append(f"\n  * Stock: {stock_code}")
            report.append(f"    Decision: {summary.get('decision', 'N/A')}")
            report.append(f"    Reasoning: {summary.get('reasoning', 'N/A')}")
            if 'confidence' in summary:
                report.append(f"    Confidence: {summary.get('confidence')}")
    else:
        report.append("  - No new analyses were performed today.")

    report.append(f"\n====== End of Report ======")
    
    report_path = os.path.join(config.LOG_DIR, "reports")
    os.makedirs(report_path, exist_ok=True)
    report_file = os.path.join(report_path, f"report_{today_str}.txt")
    
    try:
        with open(report_file, 'w', encoding='utf-8') as f:
            f.write("\n".join(report))
        print(f"Daily report saved to {report_file}")
        return report_file
    except Exception as e:
        print(f"Failed to save daily report: {e}")
        return None


def format_balance_for_slack(balance_info: dict, baseline_assets: float = None) -> str:
    """계좌 잔고와 포트폴리오 현황을 슬랙 메시지 형식으로 만듭니다."""
    if not balance_info or 'total_assets' not in balance_info:
        return "⚠️ 계좌 잔고 정보를 가져오는 데 실패했습니다."

    # 숫자를 원화(₩) 형식으로 포맷팅
    total_assets = balance_info.get('total_assets', 0)
    cash_balance = balance_info.get('cash_balance', 0)
    portfolio_kr = balance_info.get('portfolio_kr', [])
    portfolio_us = balance_info.get('portfolio_us', [])
    prev_day_assets = balance_info.get('prev_day_assets', 0)
    
    change_line = ""
    # 기준점이 있고, 0보다 클 때만 변동량을 계산
    if baseline_assets and baseline_assets > 0:
        asset_change = total_assets - baseline_assets
        asset_change_percent = (asset_change / baseline_assets) * 100
        indicator = "🔺" if asset_change >= 0 else "🔻"
        change_line = f"\n- 전일 대비: `{indicator} ₩{asset_change:,.0f} ({asset_change_percent:+.2f}%)`"    

    # Slack 메시지 라인 생성
    header = [
        "📊 *일일 계좌 현황*",
        f"*- 총 자산:* ₩{total_assets:,.0f}, {change_line}",
        f"*- 예수금:* ₩{cash_balance:,.0f}",
        "---",
        "*📈 보유 포트폴리오:*"
    ]
    
    portfolio_lines = []
    if portfolio_kr:
        portfolio_lines.append("\n--- 🇰🇷 *국내 보유 현황* ---")
        for stock in portfolio_kr:
            pnl_percent = stock.get('pnl_percent', 0)
            indicator = "🔺" if pnl_percent >= 0 else "🔻"
            portfolio_lines.append(f"  {indicator} `{stock.get('stock_code')}` | {stock.get('quantity')}주 | 수익률: `{pnl_percent:+.2f}%`")
    
    if portfolio_us:
        portfolio_lines.append("\n--- 🇺🇸 *해외 보유 현황* ---")
        for stock in portfolio_us:
            pnl_percent = stock.get('pnl_percent', 0)
            indicator = "🔺" if pnl_percent >= 0 else "🔻"
            portfolio_lines.append(f"  {indicator} `{stock.get('stock_code')}` | {stock.get('quantity')}주 | 수익률: `{pnl_percent:+.2f}%`")
    
    return '\n'.join(header + portfolio_lines)

def format_review_for_slack(review_results: list) -> str:
    """
    심층 포트폴리오 재평가 결과를 슬랙 메시지 형식으로 만듭니다.
    """
    if not review_results:
        return "✅ *포트폴리오 심층 재평가 완료*: 모든 보유 포지션이 여전히 유효한 것으로 평가되었습니다."

    message_lines = ["🔬 *포트폴리오 심층 재평가 결과*"]
    for review in review_results:
        msg = (f"--- \n"
               f"🔍 *종목*: {review.get('stock_code')}\n"
               f"🚨 *추천*: *{review.get('recommendation')}*\n"
               f"💬 *사유*: _{review.get('reasoning_for_change', 'N/A')}_")
        message_lines.append(msg)
    
    return "\n".join(message_lines)
