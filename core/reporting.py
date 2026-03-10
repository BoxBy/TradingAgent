import os
from datetime import datetime
import config
from core import ticker_utils

def generate_daily_report(portfolio_status: dict, trades_log: list, analysis_summaries: dict) -> str:
    """Summarizes daily trades and analysis results into a text report and saves it."""
    report_lines = [
        f"📊 *Daily Trading Report* - {datetime.now().strftime('%Y-%m-%d')}",
        "═" * 30,
        "",
        "🔍 *Portfolio Summary*",
        f"• Total Assets: ₩{portfolio_status.get('total_assets', 'N/A'):,}",
        f"• Cash Balance: ₩{portfolio_status.get('cash_balance', 'N/A'):,}",
        f"• Number of Holdings: {len(portfolio_status.get('portfolio', []))}",
        "",
        "📈 *Today's Trade Logs*",
    ]
    if trades_log:
        for t in trades_log:
            ticker = ticker_utils.format_for_slack(t.get('stock_code', ''))
            action_icon = "🔵" if t.get('action') == "BUY" else "🔴"
            report_lines.append(f"{action_icon} {t.get('action', '?')} {ticker} | {t.get('quantity', 0)} shares @ ₩{t.get('price', 0):,}")
    else:
        report_lines.append("_No trades today._")
    
    report_lines.append("")
    report_lines.append("📝 *Analysis Summaries*")
    for stock, summary in analysis_summaries.items():
        report_lines.append(f"• *{ticker_utils.format_for_slack(stock)}*: {summary[:120]}...")
    
    report_text = "\n".join(report_lines)
    
    # Save to file
    report_path = os.path.join(config.LOG_DIR, f"daily_report_{datetime.now().strftime('%Y%m%d')}.txt")
    with open(report_path, "w", encoding="utf-8") as f:
        f.write(report_text)
    return report_text

def format_asset_line(label: str, current_assets: float, baseline_assets: float) -> str:
    """Creates a string line showing asset changes."""
    if baseline_assets and baseline_assets != 0:
        change = current_assets - baseline_assets
        pct = (change / baseline_assets) * 100
        arrow = "📈" if change >= 0 else "📉"
        return f"{arrow} *{label}*: ₩{current_assets:,.0f} ({pct:+.2f}%)"
    return f"💰 *{label}*: ₩{current_assets:,.0f}"

def format_balance_for_slack(balance_info: dict, baseline_assets_dict: dict = {}) -> str:
    """Formats account balance and portfolio status for Slack."""
    lines = ["📦 *Account Status Report*"]
    total = balance_info.get("total_assets", 0)
    cash_krw = balance_info.get("cash_balance", 0)
    cash_usd = balance_info.get("cash_usd", 0)
    us_assets_krw = balance_info.get("us_assets_krw", 0)
    exchange_rate = balance_info.get("exchange_rate", 1350.0)

    baseline = baseline_assets_dict.get("total_assets", 0)
    lines.append(format_asset_line("Total Assets", total, baseline))

    # Portfolio breakdown by market
    portfolio = balance_info.get("portfolio", [])
    kr_holdings_val = sum(i.get("current_price", 0) * i.get("quantity", 0) for i in portfolio if i.get("market_type") == "KR")
    us_holdings_val_usd = sum(i.get("current_price", 0) * i.get("quantity", 0) for i in portfolio if i.get("market_type") == "US")

    lines.append(f"🇰🇷 *Domestic*: ₩{kr_holdings_val + cash_krw:,.0f} (Cash: ₩{cash_krw:,.0f})")
    lines.append(f"🇺🇸 *Overseas*: ₩{us_assets_krw:,.0f} (Portfolio+Cash, Rate: {exchange_rate:,.1f})")

    if portfolio:
        lines.append(f"\n📊 *Current Holdings* ({len(portfolio)})")
        for item in portfolio:
            code = item.get("stock_code", "?")
            name = ticker_utils.format_for_slack(code)
            qty = item.get("quantity", 0)
            pnl = item.get("pnl_percent", 0)
            market = "🇺🇸" if item.get("market_type") == "US" else "🇰🇷"
            arrow = "🟢" if pnl >= 0 else "🔴"
            lines.append(f"{arrow} {market} {name}: {qty} shares ({pnl:+.2f}%)")
    else:
        lines.append("\n_No holdings._")

    return "\n".join(lines)

def format_review_for_slack(review_results: list) -> str:
    """Formats deep portfolio review results for Slack."""
    if not review_results:
        return "🔍 *Portfolio Review*: No analysis data available."
    
    lines = ["🔍 *Deep Portfolio Analysis Report*"]
    for r in review_results:
        code = r.get("stock_code", "?")
        name = ticker_utils.format_for_slack(code)
        action = r.get("recommendation", "HOLD")
        reason = r.get("reasoning", "")[:100]
        
        icon = "⚡" if action in ["BUY", "SELL"] else "🔔"
        lines.append(f"{icon} *{name}*: _{action}_ — {reason}...")
        
    return "\n".join(lines)

def format_decisions_for_slack(decisions: list, market_type: str, buy_threshold: float = None, sell_threshold: float = None) -> str:
    """Formats final trade decisions for Slack."""
    if not decisions:
        return f"📋 *{market_type} Market*: No valid trading signals detected."
    
    header = f"📋 *{market_type} Trading Signals*"
    if buy_threshold is not None:
        header += f" (Buy Gate: {buy_threshold}, Sell Gate: {sell_threshold})"
    
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
        lines.append(f"{icon} *{name}*: {action} {qty} shares (Score: {score})")
        
    if len(lines) == 1:
        return f"📋 *{market_type} Market*: Trading decisions deferred (HOLD)."
        
    return "\n".join(lines)
