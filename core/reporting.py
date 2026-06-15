import os
import json
from datetime import datetime
import config
from core import ticker_utils

# Genesis index values (2026-03-09 launch date)
KOSPI_GENESIS = 5583.9
SPX_GENESIS = 6795.99

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
    """Creates a string line showing asset changes with delta symbols."""
    if baseline_assets and baseline_assets != 0:
        change = current_assets - baseline_assets
        pct = (change / baseline_assets) * 100
        # Use user-requested symbols: 🔺 (up), 🔻 (down)
        arrow = "🔺" if change >= 0 else "🔻"
        return f"*{label}*: ₩{current_assets:,.0f}, 이전 대비: {arrow} ₩{abs(change):,.0f} ({pct:+.2f}%)"
    return f"*{label}*: ₩{current_assets:,.0f}"

def format_foreign_asset_line(label: str, current_krw: float, current_usd: float, baseline_usd: float, tradeable_usd: float = None, baseline_krw: float = None) -> str:
    """Creates a string line showing foreign asset changes. Return is USD-based only (KRW FX noise filtered)."""
    base = f"*{label}*: ₩{current_krw:,.0f} (${current_usd:,.2f})"
    if tradeable_usd is not None:
        base += f", 거래 가능: ${tradeable_usd:,.2f}"
    if baseline_usd and baseline_usd > 0:
        change_usd = current_usd - baseline_usd
        pct_usd = (change_usd / baseline_usd) * 100
        arrow_usd = "🔺" if change_usd >= 0 else "🔻"
        base += f", 이전 대비: {arrow_usd} ${abs(change_usd):,.2f} ({pct_usd:+.2f}%)"
    return base

def _fetch_index_values() -> dict:
    """Fetch current KOSPI and S&P 500 closing values. Uses daily cache in logs/."""
    cache_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "logs")
    today_str = datetime.now().strftime("%Y%m%d")
    cache_path = os.path.join(cache_dir, f"index_cache_{today_str}.json")

    # Return cached value if available for today
    if os.path.exists(cache_path):
        try:
            with open(cache_path, "r") as f:
                return json.load(f)
        except Exception:
            pass

    # Hardcoded latest close values (update periodically or replace with live fetch)
    # Sourced: KOSPI 7271.66 (5/19 close), S&P 500 7353.61 (5/19 close, WSJ)
    values = {
        "kospi": 7271.66,
        "sp500": 7353.61,
        "date": today_str,
    }

    # Try to fetch via web search for freshness
    try:
        import urllib.request
        # Attempt Yahoo Finance API for S&P 500
        url = "https://query1.finance.yahoo.com/v8/finance/chart/%5EGSPC?range=1d&interval=1d"
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read().decode())
            close_val = data["chart"]["result"][0]["meta"]["regularMarketPrice"]
            if close_val and close_val > 0:
                values["sp500"] = float(close_val)
    except Exception:
        pass

    try:
        import urllib.request
        # Attempt Yahoo Finance API for KOSPI
        url = "https://query1.finance.yahoo.com/v8/finance/chart/%5EKS11?range=1d&interval=1d"
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read().decode())
            close_val = data["chart"]["result"][0]["meta"]["regularMarketPrice"]
            if close_val and close_val > 0:
                values["kospi"] = float(close_val)
    except Exception:
        pass

    # Cache for the day
    try:
        os.makedirs(cache_dir, exist_ok=True)
        with open(cache_path, "w") as f:
            json.dump(values, f)
    except Exception:
        pass

    return values


def _build_benchmark_line(genesis_dict: dict, balance_info: dict, genesis_kr_total: float = 0,
                          genesis_us_total_usd: float = 0) -> str:
    """Build benchmark comparison line: KOSPI vs KR portfolio, S&P 500 vs US portfolio.

    Args:
        genesis_dict: Genesis baseline dict with kr_total, us_total_usd, total_assets
        balance_info: Current balance info dict
        genesis_kr_total: Override for genesis KR total (takes priority over genesis_dict)
        genesis_us_total_usd: Override for genesis US total in USD
    """
    genesis_kr = genesis_kr_total or genesis_dict.get("kr_total", 0)
    genesis_us_usd = genesis_us_total_usd or genesis_dict.get("us_total_usd", 0)

    if not genesis_kr and not genesis_us_usd:
        return None  # No genesis data, skip benchmark

    kr_total = balance_info.get("kr_total", 0)
    us_total_usd = balance_info.get("us_total_usd", 0)

    index_values = _fetch_index_values()
    kospi_now = index_values.get("kospi", 0)
    sp500_now = index_values.get("sp500", 0)

    parts = []

    # KOSPI vs KR portfolio
    if genesis_kr > 0 and kospi_now > 0:
        kr_return = ((kr_total - genesis_kr) / genesis_kr) * 100
        kospi_return = ((kospi_now / KOSPI_GENESIS) - 1) * 100
        diff = kr_return - kospi_return
        outperform = "아웃" if diff > 0 else "언더"
        diff_arrow = "▲" if diff > 0 else "▼"
        parts.append(
            f"KOSPI {kospi_return:+.1f}% vs 국장 {kr_return:+.1f}% ({diff_arrow}{abs(diff):.1f}p {outperform})"
        )

    # S&P 500 vs US portfolio
    if genesis_us_usd > 0 and sp500_now > 0:
        us_return = ((us_total_usd - genesis_us_usd) / genesis_us_usd) * 100
        spx_return = ((sp500_now / SPX_GENESIS) - 1) * 100
        diff = us_return - spx_return
        outperform = "아웃" if diff > 0 else "언더"
        diff_arrow = "▲" if diff > 0 else "▼"
        parts.append(
            f"S&P500 {spx_return:+.1f}% vs 미장 {us_return:+.1f}% ({diff_arrow}{abs(diff):.1f}p {outperform})"
        )

    if not parts:
        return None

    return "📊 *벤치마크*: " + " | ".join(parts)


def _load_strategy_target() -> float:
    """Load monthly return target from strategy.json; defaults to 20%."""
    try:
        import json, os
        strategy_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "strategy.json")
        with open(strategy_path, "r") as f:
            strategy = json.load(f)
        return float(strategy.get("monthly_return_target_percent", 20.0))
    except Exception:
        return 20.0

def format_balance_for_slack(balance_info: dict, baseline_assets_dict: dict = {},
                             genesis_dict: dict = {}, monthly_dict: dict = {},
                             rolling_30d_dict: dict = {}) -> str:
    """Formats account balance and portfolio status for Slack."""
    lines = ["📦 *Account Status Report*"]
    total = balance_info.get("total_assets", 0)
    kr_total = balance_info.get("kr_total", 0)
    us_total_krw = balance_info.get("us_total_krw", 0)
    us_total_usd = balance_info.get("us_total_usd", 0)
    cash_krw = balance_info.get("cash_balance", 0)
    cash_usd = balance_info.get("cash_usd", 0)
    exchange_rate = balance_info.get("exchange_rate", 1350.0)
    portfolio = balance_info.get("portfolio", [])

    # Fallback to manual sum if payload is missing new keys
    if kr_total == 0 and us_total_krw == 0:
        kr_holdings_val = sum(i.get("current_price", 0) * i.get("quantity", 0) for i in portfolio if i.get("market_type") == "KR")
        us_holdings_usd = sum(i.get("current_price", 0) * i.get("quantity", 0) for i in portfolio if i.get("market_type") == "US")
        kr_total = kr_holdings_val + cash_krw
        us_total_usd = cash_usd + us_holdings_usd
        us_total_krw = us_total_usd * exchange_rate
        if total == 0:
            total = kr_total + us_total_krw

    baseline_total = baseline_assets_dict.get("total_assets", 0)
    baseline_kr = baseline_assets_dict.get("kr_total", 0)
    
    # Graceful degradation for USD baseline
    baseline_us_usd = baseline_assets_dict.get("us_total_usd")
    baseline_us_krw = baseline_assets_dict.get("us_total", baseline_assets_dict.get("last_get_us_total_assets_krw", 0))
    if baseline_us_usd is None:
        baseline_us_usd = baseline_us_krw / exchange_rate if exchange_rate else 0.0

    lines.append(format_asset_line("총 자산", total, baseline_total))
    lines.append(format_asset_line("국내 자산", kr_total, baseline_kr))
    lines.append(format_foreign_asset_line("해외 자산", us_total_krw, us_total_usd, baseline_us_usd, tradeable_usd=cash_usd, baseline_krw=baseline_us_krw))
    
    lines.append(f"   (환율: {exchange_rate:,.1f})")

    # --- Rolling D-30 Return (vs 30-day-ago baseline) ---
    rolling_baseline_total = rolling_30d_dict.get("total_assets", 0)
    rolling_lookback = rolling_30d_dict.get("lookback_days", 0)
    rolling_date = rolling_30d_dict.get("date", "?")
    monthly_target = _load_strategy_target()
    if rolling_baseline_total and rolling_baseline_total > 0:
        rolling_change = total - rolling_baseline_total
        rolling_pct = (rolling_change / rolling_baseline_total) * 100
        target_pct_of = (rolling_pct / monthly_target) * 100 if monthly_target else 0
        rolling_arrow = "🔺" if rolling_change >= 0 else "🔻"
        lines.append(f"📈 *D-{rolling_lookback} 수익률* ({rolling_date}~): {rolling_arrow} ₩{abs(rolling_change):,.0f} ({rolling_pct:+.2f}%) → 목표 {monthly_target:.0f}% 대비 {target_pct_of:.1f}% 달성")
    elif genesis_dict.get("total_assets", 0) > 0:
        # Fallback: use genesis baseline if no rolling data yet
        genesis_baseline_total = genesis_dict.get("total_assets", 0)
        launch_change = total - genesis_baseline_total
        launch_pct = (launch_change / genesis_baseline_total) * 100
        target_pct_of = (launch_pct / monthly_target) * 100 if monthly_target else 0
        launch_arrow = "🔺" if launch_change >= 0 else "🔻"
        lines.append(f"📈 *Since Launch*: {launch_arrow} ₩{abs(launch_change):,.0f} ({launch_pct:+.2f}%) → 목표 {monthly_target:.0f}% 대비 {target_pct_of:.1f}% 달성")

    # --- Since Launch Return (vs genesis baseline) ---
    genesis_baseline_total = genesis_dict.get("total_assets", 0)
    if genesis_baseline_total and genesis_baseline_total > 0:
        launch_change = total - genesis_baseline_total
        launch_pct = (launch_change / genesis_baseline_total) * 100
        launch_arrow = "🔺" if launch_change >= 0 else "🔻"
        launch_date = genesis_dict.get("date", "?")
        lines.append(f"🚀 *Since Launch* ({launch_date}): {launch_arrow} ₩{abs(launch_change):,.0f} ({launch_pct:+.2f}%)")

    # --- Benchmark Comparison (KOSPI vs 국장, S&P500 vs 미장) ---
    try:
        benchmark_line = _build_benchmark_line(genesis_dict, balance_info)
        if benchmark_line:
            lines.append(benchmark_line)
    except Exception:
        pass  # Gracefully skip benchmark if anything fails

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
