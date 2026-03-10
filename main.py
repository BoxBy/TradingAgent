import os
import sys
import asyncio
import argparse
import json
import pandas as pd
from mcp_bridge.client import MCPBridge
from teams.orchestrator import OrchestratorAgent
from core.notification import send_notification
from core import reporting
from core.tools import get_kis_client, dispatch_core_tool

async def main():
    parser = argparse.ArgumentParser(description="TradingClaw Autonomous Runner")
    parser.add_argument("--daemon", action="store_true", help="Run in continuous autonomous loop")
    parser.add_argument("--objective", type=str, help="Specific objective for one-off run")
    parser.add_argument("--interval", type=int, default=15, help="Minutes between cycles in daemon mode (default: 15)")
    args = parser.parse_args()

    print(f"[TradingClaw] Initializing system... (Cycle interval: {args.interval} minutes)")
    
    soul_prompt = ""
    try:
        with open("SOUL.md", "r", encoding="utf-8") as f:
            soul_prompt = f.read()
    except:
        soul_prompt = "You are a quantitative trading agent."
        print("[TradingClaw] Warning: SOUL.md file not found.")

    try:
        with open("MEMORY.md", "r", encoding="utf-8") as f:
            memory_context = f.read()
    except:
        memory_context = ""

    system_prompt = f"{soul_prompt}\n\n[Auto-Memory Context]\n{memory_context}"

    print("[TradingClaw] Connecting to KIS MCP Bridge (Python Native Mode)...")
    mcp_env = os.environ.copy()
    
    server_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "mcp_bridge", "server_kis.py")
    bridge = MCPBridge(
        server_command="python3",
        server_args=[server_path],
        env=mcp_env
    )
    
    try:
        await bridge.connect()
        print("[TradingClaw] ✅ KIS MCP Server Connection Successful")
    except Exception as e:
        print(f"[TradingClaw] ⚠️ KIS MCP Bridge Connection Failed: {e}")
        bridge = None

    orchestrator = OrchestratorAgent(system_prompt=system_prompt, mcp_bridge=bridge)
    
    from data.crawler import MarketCrawler
    from teams.emergency import EmergencyAgent
    emergency_agent = EmergencyAgent(crawler=MarketCrawler())
    
    async def emergency_loop():
        while True:
            await asyncio.sleep(60 * 10)  # Check every 10 minutes
            try:
                await emergency_agent.check_portfolio_news_emergency()
            except Exception as e:
                print(f"Emergency monitor error: {e}")
            
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
        
    print("\n[TradingClaw] ✅ System Ready --- Autonomous Trading Started ---\n")
    send_notification("🚀 *TradingClaw system has started.*")

    async def run_cycle(target_objective=None):
        if not target_objective:
            from core.market_hours import is_kr_market_open, is_us_market_open
            kr_open = is_kr_market_open()
            us_open = is_us_market_open()

            if kr_open and not us_open:
                target_objective = "Korean market is open. Scan for high-turnover opportunities in the Korean tech/semiconductor sectors. Review portfolio for 1.5% profit-taking and identify 3-5 NEW high-conviction buying opportunities."
            elif us_open and not kr_open:
                target_objective = "US market is open. Scan for high-momentum US tech/AI/growth stocks. Review portfolio for 1.5% profit-taking and identify 3-5 NEW high-conviction buying opportunities."
            elif kr_open and us_open:
                target_objective = "Both Korean and US markets are open. Perform a comprehensive scan of high-momentum tech stocks in both markets. Review portfolio for 1.5% gross profit exits."
            else:
                target_objective = "All markets are closed. Review portfolio status and build a watchlist for the next trading session focusing on new market leaders with strong catalysts."
        else:
            target_objective = "Perform standard market scan. Prioritize DISCOVERY of 3-5 new high-turnover ticker opportunities. Review portfolio for 1.5% gross profit execution."
        
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
                            print(f"[TradingClaw] {tool_name} failed (Attempt {attempt+1}/{max_retries}): {e}", file=sys.stderr)
                            await asyncio.sleep(delay)
                        else:
                            print(f"[TradingClaw] {tool_name} Final Failure: {e}", file=sys.stderr)
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

            # 1.1. Portfolio Sync (Sync active_trades.json with KIS API)
            try:
                from core.monitor import TradeMonitor
                monitor = TradeMonitor(state_file="logs/active_trades.json")
                if monitor.sync_with_broker_portfolio(portfolio):
                    print(f"[TradingClaw] Portfolio synced with broker. active_trades.json updated.")
            except Exception as e:
                print(f"[TradingClaw] Portfolio Sync failed: {e}")

            balance_info = {
                "total_assets": cash_krw + us_assets_krw,
                "cash_balance": cash_krw,
                "cash_usd": cash_usd,
                "us_assets_krw": us_assets_krw,
                "exchange_rate": exchange_rate,
                "portfolio": portfolio,
            }

            try:
                os.makedirs("logs", exist_ok=True)
                with open("logs/cached_balance.json", "w") as f:
                    json.dump({
                        "last_get_portfolio": pf_raw,
                        "last_get_buyable_cash": cash_raw,
                        "last_get_us_total_assets_krw": us_assets_raw,
                        "last_get_us_cash": us_cash_raw,
                        "last_get_exchange_rate": rate_raw,
                    }, f, default=str)
            except: pass

            baseline = {}
            if os.path.exists("logs/baseline_assets.json"):
                try:
                    with open("logs/baseline_assets.json", "r") as f:
                        baseline = json.load(f)
                except: pass

            report_msg = reporting.format_balance_for_slack(balance_info, baseline)
            send_notification(f"🔄 *New Trading Cycle Started*\n{report_msg}")
        except Exception as e:
            print(f"[TradingClaw] ⚠️ Balance report error: {e}", file=sys.stderr)

        print(f"\n{'='*60}\n[Cycle Start] {target_objective}\n{'='*60}\n")
        try:
            report = await orchestrator.run(target_objective)
            clean_report = report.replace("\\n", "\n").replace("\n\n\n", "\n\n")
            print(f"\n{'='*60}\n[Cycle Report]\n{'='*60}\n{clean_report}")
            send_notification(f"🏁 *Cycle Completion Report*\n{clean_report}")
        except Exception as e:
            print(f"[TradingClaw] ❌ Cycle Error: {e}")

    if args.daemon:
        print(f"[TradingClaw] Daemon mode started (Interval: {args.interval} minutes)")
        while True:
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
