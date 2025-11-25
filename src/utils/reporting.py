import os
from datetime import datetime

from .. import config
from . import ticker_utils


def generate_daily_report(
    portfolio_status: dict, trades_log: list, analysis_summaries: dict
):
    """
    하루의 거래 내역과 분석 결과를 요약하여 텍스트 리포트를 생성하고 저장합니다.
    """
    report = []
    today_str = datetime.now().strftime("%Y-%m-%d")
    report.append(f"====== Daily Trading Report: {today_str} ======")
    report.append("\n--- 1. Portfolio Status ---")

    if portfolio_status and "total_assets" in portfolio_status:
        report.append(
            f"Total Assets: {portfolio_status.get('total_assets', 0):,.0f} KRW"
        )
        report.append(
            f"Cash Balance: {portfolio_status.get('cash_balance', 0):,.0f} KRW"
        )
        if portfolio_status.get("portfolio"):
            for stock in portfolio_status.get("portfolio"):
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
            if "confidence" in summary:
                report.append(f"    Confidence: {summary.get('confidence')}")
    else:
        report.append("  - No new analyses were performed today.")

    report.append("\n====== End of Report ======")

    report_path = os.path.join(config.LOG_DIR, "reports")
    os.makedirs(report_path, exist_ok=True)
    report_file = os.path.join(report_path, f"report_{today_str}.txt")

    try:
        with open(report_file, "w", encoding="utf-8") as f:
            f.write("\n".join(report))
        print(f"Daily report saved to {report_file}")
        return report_file
    except Exception as e:
        print(f"Failed to save daily report: {e}")
        return None


def format_asset_line(label: str, current_assets: float, baseline_assets: float) -> str:
    """자산 변동을 나타내는 한 줄의 문자열을 생성하는 헬퍼 함수입니다."""
    change_line = ""
    # 기준 자산이 0보다 클 때만 변동률 계산
    if baseline_assets > 0:
        asset_change = current_assets - baseline_assets
        asset_change_percent = (asset_change / baseline_assets) * 100
        indicator = "🔺" if asset_change >= 0 else "🔻"
        # 전일 대비 변동률 텍스트 생성
        change_line = f", 전일 대비: `{indicator} ₩{asset_change:,.0f} ({asset_change_percent:+.2f}%)`"

    if current_assets == 0:
        return ""
    return f"*- {label}:* ₩{current_assets:,.0f}{change_line}"


def format_balance_for_slack(
    balance_info: dict, baseline_assets_dict: dict = {}
) -> str:
    """계좌 잔고와 포트폴리오 현황을 슬랙 메시지 형식으로 만듭니다."""
    if not balance_info or "total_assets" not in balance_info:
        return "⚠️ 계좌 잔고 정보를 가져오는 데 실패했습니다."

    # 숫자를 원화(₩) 형식으로 포맷팅
    total_assets = balance_info.get("total_assets", 0)
    kr_assets = balance_info.get("kr_total_krw", 0)
    us_assets = balance_info.get("us_total_krw", 0)
    cash_balance = balance_info.get("cash_balance", 0)
    portfolio_kr = balance_info.get("portfolio_kr", [])
    portfolio_us = balance_info.get("portfolio_us", [])
    prev_day_assets = balance_info.get("prev_day_assets", 0)

    prev_total = baseline_assets_dict.get("total", 0)
    prev_kr = baseline_assets_dict.get("kr", 0)
    prev_us = baseline_assets_dict.get("us", 0)

    kr_asset_line = format_asset_line("국내 자산", kr_assets, prev_kr)
    us_asset_line = format_asset_line("해외 자산", us_assets, prev_us)

    if kr_assets > 0 and us_assets > 0:
        total_asset_line = format_asset_line("총 자산", total_assets, prev_total)
    else:
        total_asset_line = ""

    # Slack 메시지 라인 생성
    header = [
        "📊 *일일 계좌 현황*",
        total_asset_line,
        kr_asset_line,
        us_asset_line,
        # f"*- 예수금:* ₩{cash_balance:,.0f}",
        "---",
        "*📈 보유 포트폴리오:*",
    ]

    portfolio_lines = []
    if portfolio_kr:
        portfolio_lines.append("\n--- 🇰🇷 *국내 보유 현황* ---")
        for stock in portfolio_kr:
            pnl_percent = stock.get("pnl_percent", 0)
            indicator = "🔺" if pnl_percent >= 0 else "🔻"
            portfolio_lines.append(
                f"  {indicator} `{ticker_utils.format_for_slack(stock.get('stock_code'))}` | {stock.get('quantity')}주 | 수익률: `{pnl_percent:+.2f}%`"
            )

    if portfolio_us:
        portfolio_lines.append("\n--- 🇺🇸 *해외 보유 현황* ---")
        for stock in portfolio_us:
            pnl_percent = stock.get("pnl_percent", 0)
            indicator = "🔺" if pnl_percent >= 0 else "🔻"
            portfolio_lines.append(
                f"  {indicator} `{ticker_utils.format_for_slack(stock.get('stock_code'))}` | {stock.get('quantity')}주 | 수익률: `{pnl_percent:+.2f}%`"
            )

    return "\n".join(header + portfolio_lines)


def format_review_for_slack(review_results: list) -> str:
    """
    심층 포트폴리오 재평가 결과를 슬랙 메시지 형식으로 만듭니다.
    """
    if not review_results:
        return "✅ *포트폴리오 심층 재평가 완료*: 모든 보유 포지션이 여전히 유효한 것으로 평가되었습니다."

    message_lines = ["🔬 *포트폴리오 심층 재평가 결과*"]
    for review in review_results:
        msg = (
            f"--- \n"
            f"🔍 *종목*: {review.get('stock_code')}\n"
            f"🚨 *추천*: *{review.get('recommendation')}*\n"
            f"💬 *사유*: _{review.get('reasoning_for_change', 'N/A')}_"
        )
        message_lines.append(msg)

    return "\n".join(message_lines)


def format_decisions_for_slack(
    decisions: list,
    market_type: str,
    buy_threshold: float | None = None,
    sell_threshold: float | None = None,
) -> str:
    """최종 매매 결정 목록을 슬랙 메시지 형식으로 포맷합니다."""
    try:
        mt = market_type.upper() if market_type else "ALL"
        header = [f"🧭 *Final Decisions* — 시장: {mt}", "---"]

        if not decisions:
            return "🧭 *Final Decisions* — 이번 라운드에서 새로운 매매 결정이 없습니다."

        lines = []
        for d in decisions:
            action = d.get("decision", "HOLD").upper()
            stock = d.get("stock_code", "-")
            qty = d.get("quantity", 0)
            score = d.get("conviction_score", 0)
            reason = d.get("reasoning") or d.get("reason", "N/A")

            display_action = action
            if action == "BUY" and buy_threshold is not None:
                try:
                    if float(score) < float(buy_threshold):
                        display_action = "보류(임계값 미달)"
                except Exception:
                    pass

            icon = "🟢" if action == "BUY" else ("🔴" if action == "SELL" else "⚪")
            if display_action == "보류(임계값 미달)":
                icon = "⚪"
            qty_txt = f" | 수량: {qty}" if qty else ""
            line = (
                f"{icon} `{ticker_utils.format_for_slack(stock)}` | 액션: *{display_action}* | 점수: `{score}`"
                f"{qty_txt}\n  └ 사유: _{reason}_"
            )
            lines.append(line)

        return "\n".join(header + lines)
    except Exception:
        return f"🧭 *Final Decisions* — {len(decisions)}건 생성됨 (시장: {market_type})"
