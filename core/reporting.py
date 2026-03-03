import os
from datetime import datetime
import config
from core import ticker_utils

def generate_daily_report(portfolio_status: dict, trades_log: list, analysis_summaries: dict) -> str:
    """하루의 거래 내역과 분석 결과를 요약하여 텍스트 리포트를 생성하고 저장합니다."""
    report_lines = [
        f"📊 *Daily Trading Report* - {datetime.now().strftime('%Y-%m-%d')}",
        "═" * 30,
        "",
        "🔍 *포트폴리오 요약*",
        f"• 총 자산: ₩{portfolio_status.get('total_assets', 'N/A'):,}",
        f"• 현금 잔고: ₩{portfolio_status.get('cash_balance', 'N/A'):,}",
        f"• 보유 종목 수: {len(portfolio_status.get('portfolio', []))}개",
        "",
        "📈 *오늘의 거래 내역*",
    ]
    if trades_log:
        for t in trades_log:
            ticker = ticker_utils.format_for_slack(t.get('stock_code', ''))
            action_icon = "🔵" if t.get('action') == "BUY" else "🔴"
            report_lines.append(f"{action_icon} {t.get('action', '?')} {ticker} | {t.get('quantity', 0)}주 @ ₩{t.get('price', 0):,}")
    else:
        report_lines.append("_거래 내역 없음_")
    
    report_lines.append("")
    report_lines.append("📝 *분석 요약*")
    for stock, summary in analysis_summaries.items():
        report_lines.append(f"• *{ticker_utils.format_for_slack(stock)}*: {summary[:120]}...")
    
    report_text = "\n".join(report_lines)
    
    # Save to file
    report_path = os.path.join(config.LOG_DIR, f"daily_report_{datetime.now().strftime('%Y%m%d')}.txt")
    with open(report_path, "w", encoding="utf-8") as f:
        f.write(report_text)
    return report_text

def format_asset_line(label: str, current_assets: float, baseline_assets: float) -> str:
    """자산 변동을 나타내는 한 줄의 문자열을 생성합니다."""
    if baseline_assets and baseline_assets != 0:
        change = current_assets - baseline_assets
        pct = (change / baseline_assets) * 100
        arrow = "📈" if change >= 0 else "📉"
        return f"{arrow} *{label}*: ₩{current_assets:,.0f} ({pct:+.2f}%)"
    return f"💰 *{label}*: ₩{current_assets:,.0f}"

def format_balance_for_slack(balance_info: dict, baseline_assets_dict: dict = {}) -> str:
    """계좌 잔고와 포트폴리오 현황을 슬랙 메시지 형식으로 만듭니다."""
    lines = ["📦 *계좌 현황 리포트*"]
    total = balance_info.get("total_assets", 0)
    cash_krw = balance_info.get("cash_balance", 0)
    cash_usd = balance_info.get("cash_usd", 0)
    us_assets_krw = balance_info.get("us_assets_krw", 0)
    exchange_rate = balance_info.get("exchange_rate", 1350.0)

    baseline = baseline_assets_dict.get("total_assets", 0)
    lines.append(format_asset_line("총 자산", total, baseline))

    # Portfolio breakdown by market
    portfolio = balance_info.get("portfolio", [])
    kr_holdings_val = sum(i.get("current_price", 0) * i.get("quantity", 0) for i in portfolio if i.get("market_type") == "KR")
    us_holdings_val_usd = sum(i.get("current_price", 0) * i.get("quantity", 0) for i in portfolio if i.get("market_type") == "US")

    lines.append(f"🇰🇷 *국내*: ₩{kr_holdings_val + cash_krw:,.0f} (현금: ₩{cash_krw:,.0f})")
    lines.append(f"🇺🇸 *해외*: ₩{us_assets_krw:,.0f} (포트폴리오+현금, 환율: {exchange_rate:,.1f})")

    if portfolio:
        lines.append(f"\n📊 *보유 종목* ({len(portfolio)}개)")
        for item in portfolio:
            code = item.get("stock_code", "?")
            name = ticker_utils.format_for_slack(code)
            qty = item.get("quantity", 0)
            pnl = item.get("pnl_percent", 0)
            market = "🇺🇸" if item.get("market_type") == "US" else "🇰🇷"
            arrow = "🟢" if pnl >= 0 else "🔴"
            lines.append(f"{arrow} {market} {name}: {qty}주 ({pnl:+.2f}%)")
    else:
        lines.append("\n_보유 종목 없음_")

    return "\n".join(lines)

def format_review_for_slack(review_results: list) -> str:
    """심층 포트폴리오 재평가 결과를 슬랙 메시지 형식으로 만듭니다."""
    if not review_results:
        return "🔍 *포트폴리오 리뷰*: 분석된 내용이 없습니다."
    
    lines = ["🔍 *보유 종목 심층 분석 리포트*"]
    for r in review_results:
        code = r.get("stock_code", "?")
        name = ticker_utils.format_for_slack(code)
        action = r.get("recommendation", "HOLD")
        reason = r.get("reasoning", "")[:100]
        
        icon = "⚡" if action in ["BUY", "SELL"] else "🔔"
        lines.append(f"{icon} *{name}*: _{action}_ — {reason}...")
        
    return "\n".join(lines)

def format_decisions_for_slack(decisions: list, market_type: str, buy_threshold: float = None, sell_threshold: float = None) -> str:
    """최종 매매 결정 목록을 슬랙 메시지 형식으로 포맷합니다."""
    if not decisions:
        return f"📋 *{market_type} 시장*: 유효한 매매 시그널이 발생하지 않았습니다."
    
    header = f"📋 *{market_type} 매매 시그널*"
    if buy_threshold is not None:
        header += f" (매수기준: {buy_threshold}, 매도기준: {sell_threshold})"
    
    lines = [header]
    for d in decisions:
        code = d.get("stock_code", "?")
        name = ticker_utils.format_for_slack(code)
        action = d.get("decision", "?")
        score = d.get("conviction_score", 0)
        qty = d.get("quantity", 0)
        
        if action == "HOLD":
            continue # Slack readability
            
        icon = "🔵" if action == "BUY" else "🔴" if action == "SELL" else "⚪"
        lines.append(f"{icon} *{name}*: {action} {qty}주 (지수: {score})")
        
    if len(lines) == 1:
        return f"📋 *{market_type} 시장*: 매매 결정이 보류되었습니다 (HOLD)."
        
    return "\n".join(lines)
