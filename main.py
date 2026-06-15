import os
import sys
import asyncio
import argparse
import json
from datetime import datetime, timedelta
import pytz
import pandas as pd
from mcp_bridge.client import MCPBridge
from teams.orchestrator import OrchestratorAgent
from core.notification import send_notification
from core import reporting
from core.tools import get_kis_client, dispatch_core_tool
from core.monitor import get_system_logger

# 메인 로거 — TimedRotatingFileHandler로 자정마다 로그 rotation
# print() 대신 logger 사용하여 tee 의존성 제거
logger = get_system_logger("main")

# Session-level tracker: phantom positions already notified in lightweight checks.
# Prevents spamming notifications every 60-min cycle for the same phantom tickers.
_phantom_notified_lightweight = set()

async def main():
    parser = argparse.ArgumentParser(description="TradingClaw Autonomous Runner")
    parser.add_argument("--daemon", action="store_true", help="Run in continuous autonomous loop")
    parser.add_argument("--objective", type=str, help="Specific objective for one-off run")
    parser.add_argument("--interval", type=int, default=15, help="Minutes between cycles in daemon mode (default: 15)")
    args = parser.parse_args()

    logger.info(f"[TradingClaw] Initializing system... (Cycle interval: {args.interval} minutes)")
    
    soul_prompt = ""
    try:
        with open("SOUL.md", "r", encoding="utf-8") as f:
            soul_prompt = f.read()
    except:
        soul_prompt = "You are a quantitative trading agent."
        logger.info("[TradingClaw] Warning: SOUL.md file not found.")

    try:
        with open("MEMORY.md", "r", encoding="utf-8") as f:
            memory_context = f.read()
    except:
        memory_context = ""

    system_prompt = f"{soul_prompt}\n\n[Auto-Memory Context]\n{memory_context}"

    logger.info("[TradingClaw] Connecting to KIS MCP Bridge (Python Native Mode)...")
    mcp_env = os.environ.copy()
    
    server_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "mcp_bridge", "server_kis.py")
    bridge = MCPBridge(
        server_command="python3",
        server_args=[server_path],
        env=mcp_env
    )
    
    try:
        await bridge.connect()
        logger.info("[TradingClaw] ✅ KIS MCP Server Connection Successful")
    except Exception as e:
        logger.info(f"[TradingClaw] ⚠️ KIS MCP Bridge Connection Failed: {e}")
        bridge = None

    orchestrator = OrchestratorAgent(system_prompt=system_prompt, mcp_bridge=bridge)

    # Initialize ticker name map so reports show stock names instead of codes
    from core import ticker_utils
    ticker_utils.init_ticker_map()
    
    from data.crawler import MarketCrawler
    from teams.emergency import EmergencyAgent
    emergency_agent = EmergencyAgent(crawler=MarketCrawler())
    
    async def emergency_loop():
        while True:
            await asyncio.sleep(60 * 10)  # Check every 10 minutes
            try:
                await emergency_agent.check_portfolio_news_emergency()
            except Exception as e:
                logger.info(f"Emergency monitor error: {e}")
            
    emergency_task = asyncio.create_task(emergency_loop())
    
    from teams.expert_tools import register_tools_by_group
    # 1. Orchestrator: Management + Financials + Memory
    await register_tools_by_group(orchestrator.agent, "orchestrator", mcp_bridge=bridge)
    
    orchestrator.spawn_teammates(3, system_prompt=system_prompt)
    # 2. Teammate-1: News & Discovery Specialist
    await register_tools_by_group(orchestrator.teammates[0].agent, "news_analyst")
    orchestrator.teammates[0].name = "NewsExplorer"

    # 3. Teammate-2: Technical & Chart Specialist
    await register_tools_by_group(orchestrator.teammates[1].agent, "tech_analyst")
    orchestrator.teammates[1].name = "TechChartist"

    # 4. Teammate-3: Risk & Execution Specialist
    await register_tools_by_group(orchestrator.teammates[2].agent, "risk_trader", mcp_bridge=bridge)
    orchestrator.teammates[2].name = "RiskExecuter"
        
    logger.info("\n[TradingClaw] ✅ System Ready --- Autonomous Trading Started ---\n")
    send_notification("🚀 *TradingClaw system has started.*", source="System", category="system")

    async def run_cycle(target_objective=None):
        # 0. Reload Dynamic Strategy to ensure freshest parameters
        import config
        import importlib
        importlib.reload(config)
        dynamic_rules = config.USER_RULES

        # 0.5. Dynamic TP/SL Update: Run MarketConditionExpert to get LLM-recommended
        # cycle-level targets and write them back to strategy.json so they become
        # the actual TP/SL for this cycle (used by system prompt, objectives,
        # emergency enforcement, and HeadTraderExpert).
        try:
            from teams.experts import MarketConditionExpert
            from data.crawler import MarketCrawler
            import yfinance as yf

            _mc_expert = MarketConditionExpert()
            _mc_crawler = MarketCrawler()

            # Fetch live market data for the expert
            _vix_df = yf.download("^VIX", period="5d", progress=False)
            _vix_value = _vix_df["Close"].iloc[-1].item() if not _vix_df.empty else 18.0

            _index_df = yf.download("^GSPC", period="5d", progress=False)
            _market_index = _index_df["Close"].iloc[-1].item() if not _index_df.empty else 5500.0

            _general_news = _mc_crawler.get_general_market_news("general")

            _mc_result = await _mc_expert.analyze(
                vix_value=_vix_value,
                market_index_value=_market_index,
                general_news=_general_news
            )

            # Extract recommended TP and write to strategy.json atomically
            _rec_tp = _mc_result.get("recommended_cycle_target_profit_pct")
            if _rec_tp is not None:
                try:
                    _rec_tp = float(_rec_tp)
                    if 0.5 <= _rec_tp <= 10.0:  # sanity bounds
                        config.update_strategy_field("target_profit_pct", _rec_tp)
                        logger.info(f"[TradingClaw] Dynamic TP updated: target_profit_pct → {_rec_tp}%")
                except (ValueError, TypeError):
                    logger.info(f"[TradingClaw] MarketConditionExpert TP invalid: {_rec_tp}")

            # Extract recommended SL and write to strategy.json atomically
            _rec_sl = _mc_result.get("recommended_cycle_stop_loss_pct")
            if _rec_sl is not None:
                try:
                    _rec_sl = float(_rec_sl)
                    # Normalize: store as positive value in strategy.json
                    _abs_sl = abs(_rec_sl)
                    if 1.0 <= _abs_sl <= 15.0:  # sanity bounds
                        config.update_strategy_field("stop_loss_pct", _abs_sl)
                        logger.info(f"[TradingClaw] Dynamic SL updated: stop_loss_pct → {_abs_sl}%")
                except (ValueError, TypeError):
                    logger.info(f"[TradingClaw] MarketConditionExpert SL invalid: {_rec_sl}")

            # Reload config to pick up the newly written values
            importlib.reload(config)
            dynamic_rules = config.USER_RULES

            # Log the expert's full market assessment for debugging
            logger.info(f"[TradingClaw] MarketConditionExpert: confidence={_mc_result.get('confidence')}, "
                        f"vix_threshold={_mc_result.get('dynamic_vix_threshold')}, "
                        f"buy_threshold={_mc_result.get('buy_conviction_threshold')}, "
                        f"top_sectors={_mc_result.get('top_sectors')}")

        except Exception as _mc_err:
            logger.info(f"[TradingClaw] MarketConditionExpert dynamic TP update skipped (using static): {_mc_err}")

        # Inject current rules into the core system prompt for all agents
        strategy_context = f"\n\n[Active Strategy: {config.TRADING_STYLE}]\n"
        strategy_context += f"- Target Profit: {dynamic_rules.get('target_profit_percent_per_trade')}% (GROSS)\n"
        strategy_context += f"- Max Loss (Stop Loss): -{dynamic_rules.get('max_loss_percent_per_trade')}%\n"
        strategy_context += f"- Min Cash Reserve: {dynamic_rules.get('min_cash_reserve_ratio')*100}%\n"
        strategy_context += f"- Max Investment Per Stock: {dynamic_rules.get('max_investment_per_stock'):,} KRW\n"
        
        # Format soul_prompt with current dynamic values
        try:
            formatted_soul = soul_prompt.format(
                stop_loss_pct=dynamic_rules.get('max_loss_percent_per_trade'),
                target_profit_pct=dynamic_rules.get('target_profit_percent_per_trade'),
                max_portfolio_size=dynamic_rules.get('portfolio_max_size'),
                min_cash_reserve_pct=int(dynamic_rules.get('min_cash_reserve_ratio', 0.15) * 100)
            )
        except Exception as e:
            logger.info(f"[TradingClaw] Warning: SOUL.md formatting failed: {e}")
            formatted_soul = soul_prompt

        cycle_system_prompt = f"{formatted_soul}\n\n[Auto-Memory Context]\n{memory_context}{strategy_context}"
        
        # Update Orchestrator and teammates prompts using the new update_system_prompt method
        orchestrator.agent.update_system_prompt(cycle_system_prompt)
        for tm in orchestrator.teammates:
            tm.agent.update_system_prompt(cycle_system_prompt)

        if not target_objective:
            from core.market_hours import is_kr_market_open, is_us_market_open
            kr_open = is_kr_market_open()
            us_open = is_us_market_open()

            if kr_open and not us_open:
                target_objective = f"[MARKET STATUS: KR=OPEN, US=CLOSED] Korean market is open, US market is CLOSED. Do NOT attempt to trade any US stocks. Only scan/trade KR stocks. Review KR portfolio for {dynamic_rules.get('target_profit_percent_per_trade')}% profit-taking and identify 3-5 NEW high-conviction KR buying opportunities."
            elif us_open and not kr_open:
                target_objective = f"[MARKET STATUS: KR=CLOSED, US=OPEN] US market is open, Korean market is CLOSED. Do NOT attempt to trade any KR stocks. Only scan/trade US stocks. Review US portfolio for {dynamic_rules.get('target_profit_percent_per_trade')}% profit-taking and identify 3-5 NEW high-conviction US buying opportunities."
            elif kr_open and us_open:
                target_objective = f"[MARKET STATUS: KR=OPEN, US=OPEN] Both markets are open. Scan high-momentum tech stocks in both markets. Review portfolio for {dynamic_rules.get('target_profit_percent_per_trade')}% gross profit exits."
            else:
                target_objective = "[MARKET STATUS: KR=CLOSED, US=CLOSED] All markets are closed. Do NOT attempt any trades. Review portfolio status and build a watchlist for the next trading session."
        else:
            target_objective = f"Perform standard market scan. Prioritize DISCOVERY of 3-5 new high-turnover ticker opportunities. Review portfolio for {dynamic_rules.get('target_profit_percent_per_trade')}% gross profit execution."
        
        # 1. Report Balance (Native KIS Path)
        try:
            kis = get_kis_client()
            cached_balance = {}
            if os.path.exists("logs/cached_balance.json"):
                try:
                    with open("logs/cached_balance.json", "r") as f:
                        cached_balance = json.load(f)
                except: pass

            async def call_kis_direct(tool_name: str, max_retries: int = 3):
                for attempt in range(max_retries):
                    try:
                        if tool_name == "get_portfolio": return kis.get_portfolio()
                        if tool_name == "get_buyable_cash": return kis.get_buyable_cash()
                        if tool_name == "get_us_total_assets_krw": return kis.get_us_total_assets_krw()
                        if tool_name == "get_us_cash": return kis.get_us_cash()
                        if tool_name == "get_exchange_rate": return kis.get_exchange_rate()
                        return None
                    except Exception as e:
                        delay = min(2 * (2 ** attempt), 15)
                        if attempt < max_retries - 1:
                            logger.error(f"[TradingClaw] {tool_name} failed (Attempt {attempt+1}/{max_retries}): {e}")
                            await asyncio.sleep(delay)
                        else:
                            logger.error(f"[TradingClaw] {tool_name} Final Failure: {e}")
                            return cached_balance.get(f"last_{tool_name}")

            # Fetch in parallel
            pf_raw, cash_raw, us_assets_raw, us_cash_raw, rate_raw = await asyncio.gather(
                call_kis_direct("get_portfolio"),
                call_kis_direct("get_buyable_cash"),
                call_kis_direct("get_us_total_assets_krw"),
                call_kis_direct("get_us_cash"),
                call_kis_direct("get_exchange_rate")
            )

            portfolio = pf_raw if isinstance(pf_raw, list) else []
            cash_krw = float(cash_raw) if cash_raw is not None else 0.0
            us_assets_krw = float(us_assets_raw) if us_assets_raw is not None else 0.0
            cash_usd = float(us_cash_raw) if us_cash_raw is not None else 0.0
            exchange_rate = float(rate_raw) if rate_raw is not None else 1350.0

            # Pre-filter phantom positions BEFORE calculating totals (2026-05-19 fix).
            # Previously phantom tickers (AMD, XOM) inflated us_holdings_usd, causing
            # false baseline entries and -51% MTD phantom drops.
            _phantom_tickers_early = set()
            _logs_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "logs")
            if os.path.isdir(_logs_dir):
                for _fpf in os.listdir(_logs_dir):
                    if _fpf.startswith("phantom_") and _fpf.endswith(".flag"):
                        _phantom_tickers_early.add(_fpf.replace("phantom_", "").replace(".flag", "").upper())
            if _phantom_tickers_early:
                _before_count = len(portfolio)
                portfolio = [p for p in portfolio if p.get("stock_code", "").upper() not in _phantom_tickers_early]
                _removed_early = _before_count - len(portfolio)
                if _removed_early > 0:
                    logger.info(f"[TradingClaw] Pre-filtered {_removed_early} phantom positions ({_phantom_tickers_early}) before total calc")

            # Calculate precise sums to prevent KIS total field anomalies and exchange rate noise
            kr_holdings_val = sum(i.get("current_price", 0) * i.get("quantity", 0) for i in portfolio if i.get("market_type") == "KR")
            us_holdings_usd = sum(i.get("current_price", 0) * i.get("quantity", 0) for i in portfolio if i.get("market_type") == "US")
            
            kr_total = cash_krw + kr_holdings_val
            
            # US assets: Use KIS tot_asst_amt (총자산금액_KRW) which includes:
            #   - Current holdings at market price (evlu_amt_smtl)
            #   - USD cash balance + settlement pending (frcr_evlu_tota)
            # tot_asst_amt = evlu_amt_smtl + frcr_evlu_tota (verified 2026-06-02)
            # Previously used frcr_evlu_tota which was ONLY cash/settlement, missing stocks (~₩144M).
            _kis_us_total_krw = float(us_assets_raw) if us_assets_raw is not None else 0.0
            us_holdings_krw = us_holdings_usd * exchange_rate  # position-level KRW for reference
            
            if _kis_us_total_krw > 0:
                # Trust KIS total (includes settlement pending)
                us_total_krw = _kis_us_total_krw
                us_total_usd = us_total_krw / exchange_rate if exchange_rate > 0 else 0.0
                _settlement_krw = us_total_krw - us_holdings_krw - (cash_usd * exchange_rate)
                if _settlement_krw > 100_000:  # log if significant settlement pending
                    logger.info(f"[TradingClaw] US settlement pending: ₩{_settlement_krw:,.0f} "
                                f"(total ₩{us_total_krw:,.0f} = holdings ₩{us_holdings_krw:,.0f} + cash ${cash_usd:,.2f} + settlement)")
            else:
                # Fallback: position-level calc if KIS total is unavailable
                us_total_usd = cash_usd + us_holdings_usd
                us_total_krw = us_total_usd * exchange_rate
                # If still 0, try cached value
                if us_total_krw == 0 and cached_balance and "last_get_us_total_assets_krw" in cached_balance:
                    try:
                        _cached_us_total = float(cached_balance["last_get_us_total_assets_krw"])
                        if _cached_us_total > 0:
                            logger.warning(f"[TradingClaw] US calc=0, using cached ₩{_cached_us_total:,.0f}")
                            us_total_krw = _cached_us_total
                            us_total_usd = us_total_krw / exchange_rate if exchange_rate > 0 else 0.0
                    except: pass
            
            total_assets = kr_total + us_total_krw

            # 1.1. Portfolio Sync (Sync active_trades.json with KIS API)
            try:
                from core.monitor import TradeMonitor
                monitor = TradeMonitor(state_file="logs/active_trades.json")
                if monitor.sync_with_broker_portfolio(portfolio):
                    logger.info(f"[TradingClaw] Portfolio synced with broker. active_trades.json updated.")
            except Exception as e:
                logger.info(f"[TradingClaw] Portfolio Sync failed: {e}")

            # --- Pre-filter: mark positions by market open/closed status ---
            # LLM should NOT try to trade closed-market positions
            from core.market_hours import is_market_open
            _kr_open_now = is_market_open("KR")
            _us_open_now = is_market_open("US")
            for pos in portfolio:
                mkt = pos.get("market_type", "KR")
                if mkt == "KR":
                    pos["market_status"] = "OPEN" if _kr_open_now else "CLOSED"
                elif mkt == "US":
                    pos["market_status"] = "OPEN" if _us_open_now else "CLOSED"
                else:
                    pos["market_status"] = "UNKNOWN"

            balance_info = {
                "total_assets": total_assets,
                "kr_total": kr_total,
                "us_total_krw": us_total_krw,
                "us_total_usd": us_total_usd,
                "cash_balance": cash_krw,
                "cash_usd": cash_usd,
                "cash_usd_krw": cash_usd * exchange_rate if exchange_rate > 0 else 0,
                "exchange_rate": exchange_rate,
                "portfolio": portfolio,
                "market_status": {"KR": "OPEN" if _kr_open_now else "CLOSED", "US": "OPEN" if _us_open_now else "CLOSED"},
            }

            try:
                # Filter phantom positions from cached_balance to prevent false monitoring alerts
                _cb_logs_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "logs")
                _phantom_tickers_cb = set()
                if os.path.isdir(_cb_logs_dir):
                    for _fcb in os.listdir(_cb_logs_dir):
                        if _fcb.startswith("phantom_") and _fcb.endswith(".flag"):
                            _phantom_tickers_cb.add(_fcb.replace("phantom_", "").replace(".flag", "").upper())
                _pf_filtered = [p for p in pf_raw if p.get("stock_code", "").upper() not in _phantom_tickers_cb] if _phantom_tickers_cb else pf_raw
                if _phantom_tickers_cb:
                    _removed_cb = {p.get("stock_code", "").upper() for p in pf_raw if p.get("stock_code", "").upper() in _phantom_tickers_cb}
                    if _removed_cb:
                        logger.info(f"[TradingClaw] Filtered {_removed_cb} from cached_balance (phantom)")
                os.makedirs("logs", exist_ok=True)
                with open("logs/cached_balance.json", "w") as f:
                    json.dump({
                        "last_get_portfolio": _pf_filtered,
                        "last_get_buyable_cash": cash_raw,
                        "last_get_us_total_assets_krw": us_total_krw,
                        "last_get_us_cash": us_cash_raw,
                        "last_get_exchange_rate": rate_raw,
                    }, f, default=str)
                # Update in-memory portfolio to reflect phantom filtering
                # so downstream code (notifications, SL enforcement) uses filtered data
                if _phantom_tickers_cb and _pf_filtered is not pf_raw:
                    portfolio = _pf_filtered
            except: pass

            current_date = datetime.now().strftime("%Y-%m-%d")

            # --- Asset History Recording ---
            try:
                def _append_asset_history(filepath: str, date_str: str, total_val: float):
                    history = []
                    if os.path.exists(filepath):
                        try:
                            with open(filepath, "r") as hf:
                                history = json.load(hf)
                        except: pass
                    # Only append if today's entry doesn't exist yet
                    if not history or history[-1].get("date") != date_str:
                        history.append({"date": date_str, "total_assets": total_val})
                        # Keep last 365 entries
                        if len(history) > 365:
                            history = history[-365:]
                        with open(filepath, "w") as hf:
                            json.dump(history, hf, indent=2)

                _append_asset_history("logs/asset_history.json", current_date, total_assets)
                _append_asset_history("logs/asset_history_kr.json", current_date, kr_total)
                _append_asset_history("logs/asset_history_us.json", current_date, us_total_krw)
                logger.info(f"[TradingClaw] Asset history updated: total={total_assets:,.0f} kr={kr_total:,.0f} us_krw={us_total_krw:,.0f}")
            except Exception as _ah_err:
                logger.error(f"[TradingClaw] Asset history update failed: {_ah_err}")

            baseline = {}
            if os.path.exists("logs/baseline_assets.json"):
                try:
                    with open("logs/baseline_assets.json", "r") as f:
                        baseline = json.load(f)
                except: pass
            
            # If no baseline, or first cycle of the day, save current as baseline
            # GUARD: Skip baseline reset if total_assets changed suspiciously vs previous baseline
            # This prevents KIS API glitches from corrupting the daily baseline
            _prev_baseline_total = baseline.get("total_assets", 0) if baseline else 0
            _baseline_date_ok = not baseline or baseline.get("date") != current_date
            _baseline_suspicious = False
            if _baseline_date_ok and baseline and _prev_baseline_total > 0:
                _drop_pct = (1 - total_assets / _prev_baseline_total) * 100
                _spike_pct = (total_assets / _prev_baseline_total - 1) * 100
                if _drop_pct > 30:
                    _baseline_suspicious = True
                    logger.warning(
                        f"[TradingClaw] ⚠️ Baseline reset SKIPPED: total_assets ₩{total_assets:,.0f} is "
                        f"{_drop_pct:.1f}% below previous baseline ₩{_prev_baseline_total:,.0f}. "
                        f"Likely KIS API glitch. Keeping old baseline."
                    )
                elif _spike_pct > 100:
                    _baseline_suspicious = True
                    logger.warning(
                        f"[TradingClaw] ⚠️ Baseline reset SKIPPED: total_assets ₩{total_assets:,.0f} is "
                        f"{_spike_pct:.1f}% ABOVE previous baseline ₩{_prev_baseline_total:,.0f}. "
                        f"Likely KIS API inflated fallback. Keeping old baseline."
                    )
            # --- Phantom-aware baseline: recalculate if new phantom positions detected ---
            # When a phantom flag is created mid-day (e.g., CRWD at 01:27), the baseline
            # set at midnight included that phantom position, inflating the baseline.
            # Detect this by comparing phantom tickers at baseline time vs now.
            _baseline_phantoms = set(baseline.get("phantom_tickers", [])) if baseline else set()
            _phantom_changed = baseline and baseline.get("date") == current_date and _phantom_tickers_early != _baseline_phantoms
            if _phantom_changed:
                _added_phantoms = _phantom_tickers_early - _baseline_phantoms
                logger.warning(
                    f"[TradingClaw] 👻 New phantom positions detected mid-day: {_added_phantoms}. "
                    f"Recalculating baseline (old was ₩{_prev_baseline_total:,.0f}, new is ₩{total_assets:,.0f})"
                )

            if (_baseline_date_ok and not _baseline_suspicious) or _phantom_changed:
                _reason = "phantom recalc" if _phantom_changed else ("Initializing" if not baseline else "Resetting")
                logger.info(f"[TradingClaw] {_reason} daily baseline for {current_date}")
                baseline = {
                    "total_assets": total_assets,
                    "kr_total": kr_total,
                    "us_total": us_total_krw,
                    "us_total_usd": us_total_usd,
                    "date": current_date,
                    "phantom_tickers": sorted(list(_phantom_tickers_early))
                }
                try:
                    with open("logs/baseline_assets.json", "w") as f:
                        json.dump(baseline, f)
                except: pass

            # --- Genesis baseline (first-ever baseline, created once) ---
            genesis_path = "logs/genesis_baseline.json"
            genesis_baseline = {}
            if os.path.exists(genesis_path):
                try:
                    with open(genesis_path, "r") as f:
                        genesis_baseline = json.load(f)
                except: pass
            if not genesis_baseline:
                logger.info("[TradingClaw] Initializing genesis baseline (first-ever snapshot)")
                genesis_baseline = {
                    "total_assets": total_assets,
                    "kr_total": kr_total,
                    "us_total": us_total_krw,
                    "us_total_usd": us_total_usd,
                    "date": current_date
                }
                try:
                    with open(genesis_path, "w") as f:
                        json.dump(genesis_baseline, f)
                except: pass

            # --- Monthly baseline (reset on 1st of each month) ---
            monthly_path = "logs/monthly_baseline.json"
            monthly_baseline = {}
            if os.path.exists(monthly_path):
                try:
                    with open(monthly_path, "r") as f:
                        monthly_baseline = json.load(f)
                except: pass
            current_day = datetime.now().day
            current_month_key = datetime.now().strftime("%Y-%m")
            if not monthly_baseline or monthly_baseline.get("month") != current_month_key:
                logger.info(f"[TradingClaw] {'Initializing' if not monthly_baseline else 'Resetting'} monthly baseline for {current_month_key}")
                monthly_baseline = {
                    "total_assets": total_assets,
                    "kr_total": kr_total,
                    "us_total": us_total_krw,
                    "us_total_usd": us_total_usd,
                    "month": current_month_key,
                    "date": current_date
                }
                try:
                    with open(monthly_path, "w") as f:
                        json.dump(monthly_baseline, f)
                except: pass

            # --- Rolling D-30 baseline (for target tracking, recalculated daily) ---
            rolling_30d_baseline = {}
            try:
                if os.path.exists("logs/asset_history.json"):
                    with open("logs/asset_history.json", "r") as f:
                        history = json.load(f)
                    if len(history) >= 2:
                        from datetime import timedelta
                        target_dt = datetime.now() - timedelta(days=30)
                        # Strategy: find the closest entry to 30 days ago (not just <=)
                        # This handles gaps in asset_history gracefully
                        baseline_entry = None
                        candidates = [e for e in history[:-1] if e.get("date")]
                        if candidates:
                            baseline_entry = min(
                                candidates,
                                key=lambda e: abs((datetime.strptime(e["date"], "%Y-%m-%d") - target_dt).days)
                            )
                        # Guard: if lookback exceeds 60 days, skip rolling (data too stale)
                        if baseline_entry:
                            actual_days = (datetime.now() - datetime.strptime(baseline_entry["date"], "%Y-%m-%d")).days
                            if actual_days > 60:
                                logger.info(f"[TradingClaw] Rolling baseline too old ({actual_days}d, entry={baseline_entry['date']}), skipping")
                                baseline_entry = None
                            else:
                                rolling_30d_baseline = {
                                    "total_assets": baseline_entry.get("total_assets", 0),
                                    "date": baseline_entry.get("date", "?"),
                                    "lookback_days": actual_days,
                                }
            except Exception as _re:
                logger.info(f"[TradingClaw] Rolling baseline lookup failed: {_re}")

            report_msg = reporting.format_balance_for_slack(
                balance_info, baseline,
                genesis_dict=genesis_baseline, monthly_dict=monthly_baseline,
                rolling_30d_dict=rolling_30d_baseline
            )
            send_notification(f"🔄 *New Trading Cycle Started*\n{report_msg}", source="System", category="cycle_report")
        except Exception as e:
            logger.error(f"[TradingClaw] ⚠️ Balance report error: {e}")

        # Inject balance summary into target_objective so LLM knows available capital per market
        try:
            _cash_krw = balance_info.get("cash_balance", 0)
            _cash_usd = balance_info.get("cash_usd", 0)
            _us_open = balance_info.get("market_status", {}).get("US", "CLOSED")
            _kr_open = balance_info.get("market_status", {}).get("KR", "CLOSED")
            _bal_summary = f"\n[CASH STATUS] KR Cash: ₩{_cash_krw:,.0f} | USD Cash: ${_cash_usd:,.0f}"
            if _cash_krw < 0:
                _bal_summary += f"\n⚠️ KR cash NEGATIVE — SELL KR positions before buying. Do NOT place KR BUY orders."
            if _cash_usd > 0 and _us_open == "OPEN":
                _bal_summary += f"\n✅ USD ${_cash_usd:,.0f} available for US BUY orders. Use get_us_stock_price to fetch current price, then place_order with that price."
            elif _cash_usd > 0 and _us_open == "CLOSED":
                _bal_summary += f"\n💡 USD ${_cash_usd:,.0f} available but US market is closed."
            target_objective = target_objective + _bal_summary
        except Exception:
            pass

        logger.info(f"\n{'='*60}\n[Cycle Start] {target_objective}\n{'='*60}\n")
        
        # --- Circuit Breaker: Skip orchestrator if KIS execution is persistently blocked ---
        execution_blocked = False
        block_flag_path = "logs/kis_execution_blocked.flag"
        if os.path.exists(block_flag_path):
            try:
                from datetime import datetime as _dt
                with open(block_flag_path, "r") as _bf:
                    block_info = json.load(_bf)
                block_time = _dt.fromisoformat(block_info.get("timestamp", ""))
                block_age_hours = (_dt.now() - block_time).total_seconds() / 3600
                if block_age_hours < 4.0:  # Block expires after 4 hours
                    execution_blocked = True
                    block_msg = (f"🚫 *KIS Execution Blocked*\n"
                                f"Mock trading API rejects orders: `{block_info.get('error', 'N/A')}`\n"
                                f"Blocked since: {block_info.get('timestamp', 'N/A')}\n"
                                f"Skipping LLM analysis cycle to save tokens. Flag auto-expires in {4.0 - block_age_hours:.1f}h.")
                    logger.info(f"[TradingClaw] {block_msg}")
                    send_notification(block_msg, source="System", category="system")
                    from core.error_escalation import escalate_error
                    escalate_error(
                        'KIS Execution Blocked',
                        f'KIS mock trading API rejects orders. Block since {block_info.get("timestamp", "N/A")}. Error: {block_info.get("error", "N/A")}'
                    )
                else:
                    # Flag expired, remove it
                    os.remove(block_flag_path)
                    logger.info(f"[TradingClaw] KIS block flag expired ({block_age_hours:.1f}h old). Removing flag, resuming full cycles.")
            except Exception as _e:
                logger.info(f"[TradingClaw] KIS block flag check error: {_e}")
        
        # --- Emergency SL Enforcement: Direct sell for SL-breached positions ---
        # Bypasses the LLM orchestrator TaskList to guarantee SL execution
        # Supports pending SL queue: if market is closed, queues the sell for next open
        import json as _json_sl
        _pending_sl_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "logs", "pending_sl_sells.json")

        def _load_pending_sl():
            try:
                if os.path.exists(_pending_sl_path):
                    with open(_pending_sl_path, "r") as _f:
                        return _json_sl.load(_f)
            except Exception:
                pass
            return []

        def _save_pending_sl(queue):
            try:
                with open(_pending_sl_path, "w") as _f:
                    _json_sl.dump(queue, _f)
            except Exception as _e:
                logger.error(f"[TradingClaw] Failed to save pending SL queue: {_e}")

        sl_pct = dynamic_rules.get('max_loss_percent_per_trade', 4.5)
        # --- SL/TP Enforcement: fallback when KIS returns empty portfolio ---
        # Root cause: KIS Mock API returns empty portfolio outside market hours,
        # so SL/TP enforcement was completely disabled during those periods.
        # Fix: use cached_balance.json (has last known prices + pnl_percent) as fallback.
        if not portfolio:
            _cached_bal_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "logs", "cached_balance.json")
            try:
                if os.path.exists(_cached_bal_path):
                    with open(_cached_bal_path, "r") as _cbf:
                        _cached = _json_sl.load(_cbf)
                    _cached_pf = _cached.get("last_get_portfolio", [])
                    if isinstance(_cached_pf, list) and _cached_pf:
                        portfolio = _cached_pf
                        logger.info(f"[TradingClaw] SL enforcement: KIS portfolio empty, using cached_balance.json fallback ({len(_cached_pf)} positions)")
            except Exception as _cb_err:
                logger.error(f"[TradingClaw] Failed to load cached_balance.json fallback: {_cb_err}")

        if portfolio and not execution_blocked:
            from core.market_hours import is_kr_market_open as _is_kr, is_us_market_open as _is_us
            _kr_open = _is_kr()
            _us_open = _is_us()
            sl_breaches = []
            pending_queue = _load_pending_sl()
            for pos in portfolio:
                pnl_pct = pos.get('pnl_percent', 0)
                ticker = pos.get('stock_code', '?')
                mkt = pos.get('market_type', 'KR')
                qty = int(pos.get('quantity', 0))
                if qty <= 0:
                    continue
                if pnl_pct <= -sl_pct:
                    market_open = (mkt == 'KR' and _kr_open) or (mkt == 'US' and _us_open)
                    if market_open:
                        sl_breaches.append({'ticker': ticker, 'qty': qty, 'pnl': pnl_pct, 'market': mkt})
                    else:
                        # Market closed — queue for next open session
                        # Check phantom flag before queuing (bugfix: phantom positions
                        # were being queued for SL sell even though they can't be sold)
                        _phantom_check_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "logs", f"phantom_{ticker.upper()}.flag")
                        if os.path.exists(_phantom_check_path):
                            logger.info(f"[TradingClaw] 👻 Skipping SL queue for phantom position: {ticker} ({pnl_pct:.1f}%)")
                            continue
                        already_queued = any(p.get('ticker') == ticker and p.get('market') == mkt for p in pending_queue)
                        if not already_queued:
                            pending_queue.append({
                                'ticker': ticker, 'qty': qty, 'pnl': pnl_pct,
                                'market': mkt, 'queued_at': datetime.now().isoformat()
                            })
                            logger.info(f"[TradingClaw] SL breach queued (market closed): {ticker} x{qty} ({pnl_pct:.1f}%)")

            # Also execute any previously queued pending SL sells whose market is now open
            remaining_pending = []
            for pending in pending_queue:
                p_mkt = pending.get('market', 'KR')
                market_now_open = (p_mkt == 'KR' and _kr_open) or (p_mkt == 'US' and _us_open)
                if market_now_open:
                    # Check if position still exists and still breached
                    p_ticker = pending.get('ticker', '?')
                    p_qty = pending.get('qty', 0)
                    still_held = any(
                        pos.get('stock_code') == p_ticker and int(pos.get('quantity', 0)) >= p_qty
                        for pos in portfolio
                    )
                    if still_held:
                        sl_breaches.append({
                            'ticker': p_ticker, 'qty': p_qty,
                            'pnl': pending.get('pnl', -999), 'market': p_mkt,
                            'from_pending': True
                        })
                        logger.info(f"[TradingClaw] Executing queued SL sell: {p_ticker} x{p_qty}")
                    # else: position was already sold or reduced, skip
                else:
                    remaining_pending.append(pending)

            _save_pending_sl(remaining_pending)

            # Deduplicate sl_breaches: same ticker can appear from both current portfolio
            # scan and pending queue, causing double sell attempts. First successful sell
            # removes from cached_balance, causing second attempt to fail with price=0.
            _seen_tickers = set()
            _deduped_breaches = []
            for breach in sl_breaches:
                _t = breach['ticker'].upper()
                if _t not in _seen_tickers:
                    _seen_tickers.add(_t)
                    _deduped_breaches.append(breach)
                else:
                    logger.info(f"[TradingClaw] Deduplicating SL breach: {breach['ticker']} already in sell queue")
            sl_breaches = _deduped_breaches

            if sl_breaches:
                # Pre-filter: skip positions flagged as phantom (unsellable by KIS)
                _logs_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "logs")
                _cleaned_breaches = []
                for breach in sl_breaches:
                    _phantom_flag = os.path.join(_logs_dir, f"phantom_{breach['ticker'].upper()}.flag")
                    if os.path.exists(_phantom_flag):
                        logger.warning(f"[TradingClaw] 👻 Phantom position detected: {breach['ticker']} — skipping sell, cleaning up local tracking")
                        # Remove from active_trades.json
                        _at_path = os.path.join(_logs_dir, "active_trades.json")
                        try:
                            if os.path.exists(_at_path):
                                with open(_at_path, "r") as _atf:
                                    _at_data = _json_sl.load(_atf)
                                _ticker_upper = breach['ticker'].upper()
                                if _ticker_upper in _at_data:
                                    del _at_data[_ticker_upper]
                                    with open(_at_path, "w") as _atf:
                                        _json_sl.dump(_at_data, _atf, indent=4)
                                    logger.info(f"[TradingClaw] 👻 Removed {_ticker_upper} from active_trades.json (phantom)")
                        except Exception as _at_err:
                            logger.error(f"[TradingClaw] Failed to clean active_trades.json: {_at_err}")
                        # Remove from cached_balance.json
                        _cb_path = os.path.join(_logs_dir, "cached_balance.json")
                        try:
                            if os.path.exists(_cb_path):
                                with open(_cb_path, "r") as _cbf:
                                    _cb_data = _json_sl.load(_cbf)
                                _cb_portfolio = _cb_data.get("last_get_portfolio", [])
                                _ticker_upper = breach['ticker'].upper()
                                _cb_data["last_get_portfolio"] = [
                                    p for p in _cb_portfolio if p.get("stock_code", "").upper() != _ticker_upper
                                ]
                                with open(_cb_path, "w") as _cbf:
                                    _json_sl.dump(_cb_data, _cbf)
                                logger.info(f"[TradingClaw] 👻 Removed {_ticker_upper} from cached_balance.json (phantom)")
                        except Exception as _cb_err:
                            logger.error(f"[TradingClaw] Failed to clean cached_balance.json: {_cb_err}")
                        # Also remove from pending SL queue
                        remaining_pending = [p for p in remaining_pending if p.get('ticker', '').upper() != breach['ticker'].upper()]
                        _save_pending_sl(remaining_pending)
                        # Keep phantom flag permanently — broker keeps returning phantom positions
                        # in balance inquiries, so the flag must persist to keep filtering them out.
                        # Previously deleted here, causing phantom positions to reappear every cycle.
                        send_notification(
                            f"👻 *Phantom Position Cleaned*: {breach['ticker']} x{breach['qty']} — KIS has no balance record. Removed from local tracking.",
                            source="PhantomDetector",
                            category="stop_loss_alert"
                        )
                    else:
                        _cleaned_breaches.append(breach)

                sl_breaches = _cleaned_breaches

            if sl_breaches:
                try:
                    _kis = get_kis_client()
                    for breach in sl_breaches:
                        ticker = breach['ticker']
                        qty = breach['qty']
                        pnl = breach['pnl']
                        mkt = breach['market']
                        # BUG FIX (2026-05-18): Verify qty against live KIS balance before selling.
                        # cached_balance.json can have stale qty (e.g., partial fills, prior sync failures),
                        # causing "잔고내역이 없습니다" errors and false phantom flags.
                        try:
                            _live_pf = _kis.get_portfolio()
                            for _lp in _live_pf:
                                if _lp.get('stock_code', '').upper() == ticker.upper():
                                    _live_qty = int(_lp.get('quantity', 0))
                                    if _live_qty > 0 and _live_qty != qty:
                                        logger.warning(f"[TradingClaw] Qty mismatch for {ticker}: cached={qty}, live={_live_qty}. Using live qty.")
                                        qty = _live_qty
                                        breach['qty'] = qty
                                    break
                        except Exception as _vq_err:
                            logger.warning(f"[TradingClaw] Could not verify live qty for {ticker}: {_vq_err}")
                        if qty <= 0:
                            logger.warning(f"[TradingClaw] Skipping SL sell for {ticker}: qty=0 after live verification")
                            continue
                        # For US: price=0 triggers auto-resolve from cache
                        # For KR: price=0 uses market order
                        price = 0 if mkt == 'KR' else 0  # auto-resolve
                        success = _kis.place_order(
                            code=ticker, qty=qty, price=price,
                            is_buy=False, market=mkt
                        )
                        status = "✅ SUCCESS" if success else "❌ FAILED"
                        source_tag = " (from pending queue)" if breach.get('from_pending') else ""
                        msg = f"🚨 *Emergency SL Sell*{source_tag} {status}: {ticker} x{qty} ({pnl:.1f}%, SL threshold: -{sl_pct}%)"
                        logger.info(f"[TradingClaw] {msg}")
                        send_notification(msg, source="EmergencySL", category="stop_loss_alert")
                        if success:
                            logger.info(f"[TradingClaw] Emergency sell executed: {ticker} x{qty}")
                            # Remove from pending queue if it was there
                            remaining_pending = [p for p in remaining_pending if p.get('ticker') != ticker]
                            _save_pending_sl(remaining_pending)
                            # Remove from active_trades.json
                            try:
                                _at_path = os.path.join(_logs_dir, 'active_trades.json')
                                if os.path.exists(_at_path):
                                    with open(_at_path, 'r') as _atf:
                                        _at_data = json.load(_atf)
                                    _tu = ticker.upper()
                                    if _tu in _at_data:
                                        del _at_data[_tu]
                                        with open(_at_path, 'w') as _atf:
                                            json.dump(_at_data, _atf, indent=4)
                                        logger.info(f"[TradingClaw] Removed {_tu} from active_trades.json (SL sell executed)")
                            except Exception as _at_err:
                                logger.warning(f"[TradingClaw] Failed to clean active_trades.json for {ticker}: {_at_err}")
                            # Remove from cached_balance.json to prevent phantom sell attempts
                            try:
                                _cb_path = os.path.join(_logs_dir, 'cached_balance.json')
                                if os.path.exists(_cb_path):
                                    with open(_cb_path, 'r') as _cbf:
                                        _cb_data = json.load(_cbf)
                                    if 'last_get_portfolio' in _cb_data:
                                        before = len(_cb_data['last_get_portfolio'])
                                        _cb_data['last_get_portfolio'] = [
                                            e for e in _cb_data['last_get_portfolio']
                                            if e.get('stock_code') != ticker
                                        ]
                                        after = len(_cb_data['last_get_portfolio'])
                                        if before != after:
                                            with open(_cb_path, 'w') as _cbf:
                                                json.dump(_cb_data, _cbf, indent=2)
                                            logger.info(f"[TradingClaw] Removed {ticker} from cached_balance.json (was {before} entries, now {after})")
                            except Exception as _cb_err:
                                logger.warning(f"[TradingClaw] Failed to clean cached_balance.json for {ticker}: {_cb_err}")
                        else:
                            logger.error(f"[TradingClaw] Emergency sell FAILED: {ticker} x{qty}")
                            # Check if this was a phantom position failure (flag just written by place_order)
                            _just_flagged = os.path.join(_logs_dir, f"phantom_{ticker.upper()}.flag")
                            if os.path.exists(_just_flagged):
                                logger.warning(f"[TradingClaw] 👻 Phantom position detected after sell failure: {ticker} — will be cleaned on next cycle")
                            else:
                                from core.error_escalation import escalate_error
                                escalate_error(
                                    'Emergency sell FAILED',
                                    f'Emergency stop-loss sell failed for {ticker} x{qty} (P&L: {pnl:.1f}%, SL threshold: -{sl_pct}%). Position remains at risk.'
                                )
                except Exception as _sl_err:
                    import traceback as _tb
                    logger.error(f"[TradingClaw] Emergency SL enforcement error: {_sl_err}")
                    from core.error_escalation import escalate_error
                    escalate_error(
                        'Emergency sell FAILED',
                        f'Emergency SL enforcement threw an exception: {_sl_err}',
                        _tb.format_exc()
                    )

        # --- Emergency TP Enforcement: Direct sell for positions exceeding TP target ---
        # Prevents LLM execution failures from leaving profitable positions unmanaged
        tp_pct = dynamic_rules.get('target_profit_percent_per_trade', 3.0)
        tp_multiplier = 3.0  # Emergency triggers at 3x the TP target (e.g., 9% for 3% TP)
        emergency_tp_threshold = tp_pct * tp_multiplier
        if portfolio and not execution_blocked:
            from core.market_hours import is_kr_market_open as _is_kr2, is_us_market_open as _is_us2
            _kr_open2 = _is_kr2()
            _us_open2 = _is_us2()
            tp_breaches = []
            for pos in portfolio:
                pnl_pct = pos.get('pnl_percent', 0)
                ticker = pos.get('stock_code', '?')
                mkt = pos.get('market_type', 'KR')
                if mkt == 'KR' and not _kr_open2:
                    continue
                if mkt == 'US' and not _us_open2:
                    continue
                if pnl_pct >= emergency_tp_threshold:
                    qty = int(pos.get('quantity', 0))
                    if qty > 0:
                        tp_breaches.append({'ticker': ticker, 'qty': qty, 'pnl': pnl_pct, 'market': mkt})
            if tp_breaches:
                try:
                    _kis_tp = get_kis_client()
                    for breach in tp_breaches:
                        ticker = breach['ticker']
                        qty = breach['qty']
                        pnl = breach['pnl']
                        mkt = breach['market']
                        price = 0  # auto-resolve from cache
                        success = _kis_tp.place_order(
                            code=ticker, qty=qty, price=price,
                            is_buy=False, market=mkt
                        )
                        status = "✅ SUCCESS" if success else "❌ FAILED"
                        msg = f"💰 *Emergency TP Sell* {status}: {ticker} x{qty} ({pnl:.1f}%, TP threshold: +{emergency_tp_threshold:.1f}%)"
                        logger.info(f"[TradingClaw] {msg}")
                        send_notification(msg, source="EmergencyTP", category="take_profit_alert")
                        if success:
                            logger.info(f"[TradingClaw] Emergency TP sell executed: {ticker} x{qty}")
                        else:
                            logger.error(f"[TradingClaw] Emergency TP sell FAILED: {ticker} x{qty}")
                except Exception as _tp_err:
                    import traceback as _tb2
                    logger.error(f"[TradingClaw] Emergency TP enforcement error: {_tp_err}")

        if not execution_blocked:
            try:
                report = await orchestrator.run(target_objective)
                if report:
                    clean_report = report.replace("\\n", "\n").replace("\n\n\n", "\n\n")
                    logger.info(f"\n{'='*60}\n[Cycle Report]\n{'='*60}\n{clean_report}")
                    send_notification(f"🏁 *Cycle Completion Report*\n{clean_report}", source="Orchestrator", category="cycle_report")
                else:
                    logger.warning("[TradingClaw] Orchestrator returned None — skipping report")
                    from core.error_escalation import escalate_error
                    escalate_error(
                        'Cycle Empty Report',
                        f'Orchestrator returned None for cycle: {target_objective[:200]}. '
                        f'No tasks were created or completed. Check LLM connectivity and fallback chain.',
                    )
            except Exception as e:
                import traceback as _cycle_tb
                logger.info(f"[TradingClaw] ❌ Cycle Error: {e}")
                from core.error_escalation import escalate_error
                escalate_error(
                    'Cycle Crash',
                    f'Orchestrator.run() threw: {e}. Target: {target_objective[:200]}',
                    _cycle_tb.format_exc(),
                )
        else:
            logger.info(f"[TradingClaw] ⏸️ Full cycle skipped — KIS execution blocked. Only balance report sent.")

    def get_next_market_open():
        """Return (market_name, hours_until, minutes_until) for the next market to open."""
        tz_kst = pytz.timezone('Asia/Seoul')
        now_kst = datetime.now(tz_kst)

        def next_weekday_open(now, open_hour, open_minute):
            candidate = now.replace(hour=open_hour, minute=open_minute, second=0, microsecond=0)
            if candidate <= now:
                candidate += timedelta(days=1)
            while candidate.weekday() >= 5:
                candidate += timedelta(days=1)
            return candidate

        kr_next = next_weekday_open(now_kst, 9, 0)
        us_next = next_weekday_open(now_kst, 23, 30)

        if kr_next <= us_next:
            next_time, name = kr_next, "KR (09:00 KST)"
        else:
            next_time, name = us_next, "US (23:30 KST)"

        total_seconds = int((next_time - now_kst).total_seconds())
        hours, remainder = divmod(total_seconds, 3600)
        minutes = remainder // 60
        return name, hours, minutes

    async def run_lightweight_check():
        """Portfolio check + news scan when all markets are closed.
        
        장외시간에도 뉴스 수집/분석은 수행:
        - 보유 종목 관련 뉴스 스캔 (크롤러만, LLM 없이)
        - 긴급 뉴스 발견 시 알림
        - 포트폴리오 손절가 모니터링
        """
        logger.info("[TradingClaw] Markets closed — lightweight check (portfolio + news scan)")
        
        # --- 1. 포트폴리오 손절가 체크 (기존) ---
        try:
            kis = get_kis_client()

            # Fetch portfolio for stop-loss monitoring
            portfolio = []
            for attempt in range(3):
                try:
                    pf_raw = kis.get_portfolio()
                    if isinstance(pf_raw, list):
                        portfolio = pf_raw
                    break
                except Exception as e:
                    if attempt < 2:
                        await asyncio.sleep(min(2 * (2 ** attempt), 15))
                    else:
                        logger.info(f"[TradingClaw] Portfolio fetch failed: {e}")
                        portfolio = []

            # Fallback: if KIS returns empty portfolio, use cached_balance.json
            if not portfolio:
                _cb_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "logs", "cached_balance.json")
                try:
                    if os.path.exists(_cb_path):
                        import json as _json_cb
                        with open(_cb_path, "r") as _cbf:
                            _cached = _json_cb.load(_cbf)
                        _cached_pf = _cached.get("last_get_portfolio", [])
                        if isinstance(_cached_pf, list) and _cached_pf:
                            portfolio = _cached_pf
                            logger.info(f"[TradingClaw] Lightweight check: KIS portfolio empty, using cached_balance.json fallback ({len(_cached_pf)} positions)")
                except Exception as _cb_err:
                    logger.error(f"[TradingClaw] Failed to load cached_balance.json in lightweight check: {_cb_err}")

            if portfolio:
                # --- Phantom Position Cleanup (lightweight mode) ---
                # Positions flagged as phantom can't be sold (KIS has no balance).
                # Clean them up during lightweight checks too, not just during main cycle SL processing.
                _logs_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "logs")
                _phantom_flags = [f for f in os.listdir(_logs_dir) if f.startswith("phantom_") and f.endswith(".flag")] if os.path.isdir(_logs_dir) else []
                for _pflag in _phantom_flags:
                    _ticker_from_flag = _pflag.replace("phantom_", "").replace(".flag", "").upper()
                    import json as _json_ph
                    try:
                        _flag_path = os.path.join(_logs_dir, _pflag)
                        with open(_flag_path, "r") as _fpf:
                            _flag_data = _json_ph.load(_fpf)
                        _flag_time = _flag_data.get("timestamp", "")
                        logger.warning(f"[TradingClaw] 👻 Phantom position detected in lightweight check: {_ticker_from_flag} (flagged at {_flag_time}) — cleaning up local tracking")
                        # Remove from active_trades.json
                        _at_path = os.path.join(_logs_dir, "active_trades.json")
                        if os.path.exists(_at_path):
                            with open(_at_path, "r") as _atf:
                                _at_data = _json_ph.load(_atf)
                            if _ticker_from_flag in _at_data:
                                del _at_data[_ticker_from_flag]
                                with open(_at_path, "w") as _atf:
                                    _json_ph.dump(_at_data, _atf, indent=4)
                                logger.info(f"[TradingClaw] 👻 Removed {_ticker_from_flag} from active_trades.json (phantom, lightweight)")
                        # Remove from cached_balance.json
                        _cb_path_ph = os.path.join(_logs_dir, "cached_balance.json")
                        if os.path.exists(_cb_path_ph):
                            with open(_cb_path_ph, "r") as _cbf:
                                _cb_data = _json_ph.load(_cbf)
                            _cb_portfolio = _cb_data.get("last_get_portfolio", [])
                            _cb_data["last_get_portfolio"] = [
                                p for p in _cb_portfolio if p.get("stock_code", "").upper() != _ticker_from_flag
                            ]
                            with open(_cb_path_ph, "w") as _cbf:
                                _json_ph.dump(_cb_data, _cbf, indent=4)
                            logger.info(f"[TradingClaw] 👻 Removed {_ticker_from_flag} from cached_balance.json (phantom, lightweight)")
                        # Remove from pending_sl_sells.json
                        _psl_path = os.path.join(_logs_dir, "pending_sl_sells.json")
                        if os.path.exists(_psl_path):
                            with open(_psl_path, "r") as _pslf:
                                _psl_data = _json_ph.load(_pslf)
                            _psl_data = [p for p in _psl_data if p.get("ticker", "").upper() != _ticker_from_flag]
                            with open(_psl_path, "w") as _pslf:
                                _json_ph.dump(_psl_data, _pslf, indent=4)
                            logger.info(f"[TradingClaw] 👻 Removed {_ticker_from_flag} from pending_sl_sells.json (phantom, lightweight)")
                        # Remove from local portfolio list too
                        portfolio = [p for p in portfolio if p.get("stock_code", "").upper() != _ticker_from_flag]
                        # Keep phantom flag permanently — broker keeps returning phantom positions
                        # in balance inquiries, so the flag must persist to keep filtering them out.
                        # Only send notification once per phantom per session to avoid spam.
                        if _ticker_from_flag not in _phantom_notified_lightweight:
                            _phantom_notified_lightweight.add(_ticker_from_flag)
                            send_notification(
                                f"👻 *Phantom Position Cleaned (lightweight)*: {_ticker_from_flag} — KIS has no balance record. Removed from local tracking.",
                                source="PhantomDetector",
                                category="stop_loss_alert"
                            )
                        else:
                            logger.debug(f"[TradingClaw] 👻 Phantom {_ticker_from_flag} already notified this session, skipping notification (cleanup still performed)")
                    except Exception as _ph_err:
                        logger.error(f"[TradingClaw] Phantom cleanup error for {_ticker_from_flag}: {_ph_err}")

                import config
                import importlib
                importlib.reload(config)
                sl_pct = config.USER_RULES.get('max_loss_percent_per_trade', 3.0)

                breaches = []
                sl_breach_positions = []
                for pos in portfolio:
                    entry = pos.get('average_price', 0)
                    current = pos.get('current_price', 0)
                    pnl_pct = pos.get('pnl_percent', 0)  # KIS에서 직접 제공
                    if entry > 0 and current > 0:
                        if pnl_pct == 0:  # fallback: 수동 계산
                            pnl_pct = ((current - entry) / entry) * 100
                        if pnl_pct <= -sl_pct:
                            ticker = pos.get('stock_code', '?')
                            mkt = pos.get('market_type', 'KR')
                            qty = int(pos.get('quantity', 0))
                            breaches.append(
                                f"⚠️ {ticker}: {pnl_pct:.1f}% (entry: {entry:,.0f} → now: {current:,.0f})"
                            )
                            sl_breach_positions.append({'ticker': ticker, 'qty': qty, 'pnl': pnl_pct, 'market': mkt})

                if breaches:
                    alert = f"🚨 *Stop-Loss Alert* ({len(breaches)} position(s) below -{sl_pct}%):\n" + "\n".join(breaches)
                    send_notification(alert, source="System", category="stop_loss_alert")
                    logger.info(f"[TradingClaw] {alert}")

                    # Queue SL breach positions for emergency sell when market opens
                    import json as _json_slq
                    _pending_sl_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "logs", "pending_sl_sells.json")
                    pending_queue = []
                    if os.path.exists(_pending_sl_path):
                        try:
                            with open(_pending_sl_path, "r") as _pqf:
                                pending_queue = _json_slq.load(_pqf)
                        except Exception:
                            pending_queue = []
                    for bp in sl_breach_positions:
                        already_queued = any(p.get('ticker') == bp['ticker'] and p.get('market') == bp['market'] for p in pending_queue)
                        if not already_queued:
                            pending_queue.append({
                                'ticker': bp['ticker'], 'qty': bp['qty'], 'pnl': bp['pnl'],
                                'market': bp['market'], 'queued_at': datetime.now().isoformat()
                            })
                            logger.info(f"[TradingClaw] SL breach queued for next market open: {bp['ticker']} x{bp['qty']} ({bp['pnl']:.1f}%)")
                    try:
                        with open(_pending_sl_path, "w") as _pqf:
                            _json_slq.dump(pending_queue, _pqf)
                    except Exception as _pqe:
                        logger.error(f"[TradingClaw] Failed to save pending SL queue: {_pqe}")
                else:
                    total_val = sum(p.get('current_price', 0) * p.get('quantity', 0) for p in portfolio)
                    logger.info(f"[TradingClaw] All {len(portfolio)} positions within stop-loss. Portfolio value: {total_val:,.0f}")
        except Exception as e:
            logger.error(f"[TradingClaw] Portfolio check error: {e}")

        # --- 2. 뉴스 스캔 (장외시간에도 수행) ---
        try:
            from data.crawler import MarketCrawler
            crawler = MarketCrawler()
            
            # 보유 종목 코드 수집
            held_tickers = [p.get('stock_code', '') for p in portfolio if p.get('stock_code')]
            
            news_items = []
            
            # 전체 시장 뉴스
            market_news = crawler.get_general_market_news(category="general")
            if market_news:
                news_items.extend(market_news[:20])
            
            # 보유 종목 개별 뉴스
            for ticker in held_tickers[:5]:  # 상위 5개만 (API 호출 제한)
                try:
                    stock_news = crawler.get_consolidated_stock_news(ticker, limit=5)
                    if stock_news:
                        for n in stock_news:
                            n['_ticker'] = ticker  # 태그
                        news_items.extend(stock_news)
                except Exception:
                    pass
            
            # 긴급 키워드 필터 (LLM 없이 단순 문자열 매칭)
            urgent_keywords = ['긴급', '속보', '공시', 'recall', 'recall', 'ban', ' SEC ',
                              '조회공시', '감사의견', '적자', '상장폐지', 'delist',
                              '핵심', '경고', 'warning', 'downgrade', '상한가', '하한가',
                              'emergency', 'FDA', '승인', '거부', 'breakthrough']
            
            urgent_news = []
            for n in news_items:
                headline = n.get('headline', n.get('title', ''))
                if any(kw.lower() in headline.lower() for kw in urgent_keywords):
                    # 보유 종목 관련 뉴스인지 확인
                    ticker_tag = n.get('_ticker', '')
                    is_held = any(t in headline.upper() for t in held_tickers) or ticker_tag
                    if is_held:
                        urgent_news.append(f"🚨 [{ticker_tag or '시장'}] {headline}")
            
            if urgent_news:
                alert_msg = "📰 *긴급 뉴스 감지 (보유 종목 관련)*\n" + "\n".join(urgent_news[:5])
                send_notification(alert_msg, source="NewsScanner", category="news_alert")
                logger.info(f"[TradingClaw] {alert_msg}")
            else:
                logger.info(f"[TradingClaw] News scan complete: {len(news_items)} articles scanned, no urgent alerts.")
                
        except Exception as e:
            logger.error(f"[TradingClaw] News scan error: {e}")

    if args.daemon:
        from core.market_hours import is_kr_market_open, is_us_market_open
        logger.info(f"[TradingClaw] Daemon mode started (Interval: {args.interval} minutes)")
        while True:
            kr_open = is_kr_market_open()
            us_open = is_us_market_open()

            if not kr_open and not us_open:
                await run_lightweight_check()
                market_name, hours, minutes = get_next_market_open()
                logger.info(f"[TradingClaw] Next session: {market_name} in {hours}h {minutes}m. Sleeping 60 min.")
                await asyncio.sleep(60 * 60)
            else:
                await run_cycle(args.objective)
                await asyncio.sleep(args.interval * 60)
    else:
        await run_cycle(args.objective)

    emergency_task.cancel()
    try: await emergency_task
    except: pass
    if bridge:
        try: await bridge.disconnect()
        except: pass

if __name__ == "__main__":
    import anyio
    anyio.run(main, backend="asyncio")
