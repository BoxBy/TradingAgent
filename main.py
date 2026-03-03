
import os
import sys
import asyncio
import argparse
from dotenv import load_dotenv
from mcp_bridge.client import MCPBridge
from teams.orchestrator import OrchestratorAgent
from data.rag import register_rag_tools
from data.vibe_check import register_vibe_tools
from core.notification import send_notification
from core import reporting
import json
import pandas as pd

# Load environment variables
load_dotenv()

async def main():
    parser = argparse.ArgumentParser(description="TradingClaw Autonomous Runner")
    parser.add_argument("--daemon", action="store_true", help="Run in continuous autonomous loop")
    parser.add_argument("--objective", type=str, help="Specific objective for one-off run")
    parser.add_argument("--interval", type=int, default=15, help="Minutes between cycles in daemon mode (default: 15)")
    args = parser.parse_args()

    print(f"[TradingClaw] 시스템 초기화 중... (사이클 간격: {args.interval}분)")
    
    soul_prompt = ""
    try:
        with open("SOUL.md", "r", encoding="utf-8") as f:
            soul_prompt = f.read()
    except:
        soul_prompt = "You are a quantitative trading agent."
        print("[TradingClaw] 경고: SOUL.md 파일을 찾을 수 없습니다.")

    try:
        with open("MEMORY.md", "r", encoding="utf-8") as f:
            memory_context = f.read()
    except:
        memory_context = ""

    system_prompt = f"{soul_prompt}\n\n[Auto-Memory Context]\n{memory_context}"

    print("[TradingClaw] KIS MCP 브릿지 연결 중 (Python Native Mode)...")
    mcp_env = os.environ.copy()
    
    # Use native python instead of docker
    server_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "mcp_bridge", "server_kis.py")
    bridge = MCPBridge(
        server_command="python3",
        server_args=[server_path],
        env=mcp_env
    )
    
    try:
        # In a real setup, we'd wait for connection.
        # For this autonomous runner, we'll try to connect but handle failures gracefully.
        await bridge.connect()
        print("[TradingClaw] ✅ KIS MCP 서버 연결 완료")
    except Exception as e:
        print(f"[TradingClaw] ⚠️  KIS MCP 브릿지 연결 실패: {e}")
        bridge = None

    orchestrator = OrchestratorAgent(system_prompt=system_prompt, mcp_bridge=bridge)
    
    from data.crawler import MarketCrawler
    from teams.emergency import EmergencyAgent
    emergency_agent = EmergencyAgent(crawler=MarketCrawler())
    
    async def emergency_loop():
        while True:
            await asyncio.sleep(60 * 5) # Check every 5 minutes
            try:
                await emergency_agent.check_portfolio_news_emergency()
            except Exception as e:
                print(f"Emergency monitor error: {e}")
            
    emergency_task = asyncio.create_task(emergency_loop())
    
    from teams.expert_tools import register_expert_tools
    register_rag_tools(orchestrator.agent)
    register_vibe_tools(orchestrator.agent)
    register_expert_tools(orchestrator.agent)
    
    orchestrator.spawn_teammates(3, system_prompt=system_prompt)
    for tm in orchestrator.teammates:
        register_rag_tools(tm.agent)
        register_vibe_tools(tm.agent)
        register_expert_tools(tm.agent)
        
    print("\n[TradingClaw] ✅ 시스템 준비 완료 --- 자율 매매 시작 ---\n")
    send_notification("🚀 *TradingClaw 시스템이 시작되었습니다.*")

    async def run_cycle(target_objective=None):
        # Determine which market is open and set appropriate objective
        if not target_objective:
            from core.market_hours import is_kr_market_open, is_us_market_open
            kr_open = is_kr_market_open()
            us_open = is_us_market_open()

            if kr_open and not us_open:
                target_objective = "한국 장이 열려 있습니다. 한국 기술주(반도체, 삼성전자, SK하이닉스 등)를 중심으로 시장 스캔하고 포트폴리오를 검토하세요. 1.5% 이상 수익이 있는 종목을 확인하고 신규 매수 기회를 발굴하세요."
            elif us_open and not kr_open:
                target_objective = "미국 장이 열려 있습니다. 미국 기술주(NVDA, AAPL, MSFT, AMZN, GOOGL 등)를 중심으로 시장 스캔하고 포트폴리오를 검토하세요. 1.5% 이상 수익이 있는 종목을 확인하고 신규 매수 기회를 발굴하세요."
            elif kr_open and us_open:
                target_objective = "한국 장과 미국 장이 모두 열려 있습니다. 양쪽 시장의 기술주를 모두 분석하고 포트폴리오를 검토하세요."
            else:
                target_objective = "모든 장이 마감되었습니다. 포트폴리오 현황을 검토하고 다음 개장일을 위한 준비를 하세요."
        else:
            target_objective = "Perform standard market scan. Review portfolio for 1.5% profit locks. Identify new high-turnover opportunities in US/KR tech sectors."
        
        # 1. Report Balance at Start of Cycle
        if bridge:
            try:
                # Load cached balance as fallback
                cached_balance = {}
                try:
                    if os.path.exists("logs/cached_balance.json"):
                        with open("logs/cached_balance.json", "r") as f:
                            cached_balance = json.load(f)
                except:
                    pass

                # Helper function with retry logic
                async def call_tool_with_retry(tool_name: str, max_retries: int = 3):
                    for attempt in range(max_retries):
                        try:
                            result = await bridge.call_tool(tool_name, {})
                            return result
                        except Exception as e:
                            if attempt < max_retries - 1:
                                print(f"[TradingClaw] {tool_name} 실패 (시도 {attempt+1}/{max_retries}): {e}, 재시도 중...", file=sys.stderr)
                                await asyncio.sleep(1)
                            else:
                                print(f"[TradingClaw] {tool_name} 최종 실패: {e}", file=sys.stderr)
                                # Return cached value if available
                                cached_key = f"last_{tool_name}"
                                if cached_key in cached_balance:
                                    print(f"[TradingClaw] 캐시된 값 사용: {cached_balance[cached_key]}", file=sys.stderr)
                                    return cached_balance[cached_key]
                                return None

                # Call all APIs with retry logic
                pf_raw = await call_tool_with_retry("get_portfolio")
                cash_raw = await call_tool_with_retry("get_buyable_cash")
                us_assets_raw = await call_tool_with_retry("get_us_total_assets_krw")
                us_cash_raw = await call_tool_with_retry("get_us_cash")
                rate_raw = await call_tool_with_retry("get_exchange_rate")

                # Parse results with fallback
                portfolio = []
                if pf_raw:
                    try:
                        portfolio = json.loads(pf_raw) if isinstance(pf_raw, str) else pf_raw
                        if not isinstance(portfolio, list):
                            portfolio = []
                    except:
                        portfolio = []

                cash_krw = 0
                if cash_raw:
                    try:
                        cash_krw = float(str(cash_raw).split(":")[-1].replace(",", "").strip())
                    except:
                        pass

                us_assets_krw = 0
                if us_assets_raw:
                    try:
                        us_assets_krw = float(str(us_assets_raw).split(":")[-1].replace(",", "").strip())
                    except:
                        pass

                cash_usd = 0
                if us_cash_raw:
                    try:
                        cash_usd = float(str(us_cash_raw).split(":")[-1].replace(",", "").strip())
                    except:
                        pass

                exchange_rate = 1350.0
                if rate_raw:
                    try:
                        exchange_rate = float(str(rate_raw).split(":")[-1].replace(",", "").strip())
                    except:
                        pass

                # Total assets = KR cash + US total assets (already in KRW)
                print(f"[DEBUG] Raw: cash_raw={cash_raw}, us_assets_raw={us_assets_raw}, us_cash_raw={us_cash_raw}, rate_raw={rate_raw}", file=sys.stderr)
                total_assets = cash_krw + us_assets_krw
                print(f"[DEBUG] Calculated: kr_cash={cash_krw}, us_assets={us_assets_krw}, us_cash={cash_usd}, rate={exchange_rate}, total={total_assets}", file=sys.stderr)

                balance_info = {
                    "total_assets": total_assets,
                    "cash_balance": cash_krw,
                    "cash_usd": cash_usd,
                    "us_assets_krw": us_assets_krw,
                    "exchange_rate": exchange_rate,
                    "portfolio": portfolio,
                }

                # Cache current balance for fallback
                try:
                    os.makedirs("logs", exist_ok=True)
                    with open("logs/cached_balance.json", "w") as f:
                        json.dump({
                            "last_get_portfolio": pf_raw,
                            "last_get_buyable_cash": cash_raw,
                            "last_get_us_total_assets_krw": us_assets_raw,
                            "last_get_us_cash": us_cash_raw,
                            "last_get_exchange_rate": rate_raw,
                        }, f)
                except:
                    pass

                # Load yesterday's baseline for PnL %
                baseline = {}
                try:
                    if os.path.exists("logs/baseline_assets.json"):
                        with open("logs/baseline_assets.json", "r") as f:
                            baseline = json.load(f)
                except:
                    pass

                report_msg = reporting.format_balance_for_slack(balance_info, baseline)
                send_notification(f"🔄 *새 거래 사이클 시작*\n{report_msg}")
            except Exception as e:
                print(f"[TradingClaw] ⚠️ 잔고 리포트 실패: {e}", file=sys.stderr)
                import traceback
                traceback.print_exc(file=sys.stderr)

        print(f"\n{'='*60}")
        print(f"[사이클 시작] {target_objective}")
        print(f"{'='*60}\n")
        try:
            report = await orchestrator.run(target_objective)
            # Ensure clean line breaks for Slack
            clean_report = report.replace("\\n", "\n").replace("\n\n\n", "\n\n")
            print("\n" + "="*60)
            print("[사이클 리포트]")
            print("="*60)
            print(clean_report)
            send_notification(f"🏁 *사이클 완료 리포트*\n━━━━━━━━━━━━━━━━━━━━\n{clean_report}")
        except Exception as e:
            msg = f"[TradingClaw] ❌ 사이클 오류: {e}"
            print(msg)
            send_notification(f"⚠️ {msg}")

    if args.daemon:
        print(f"Running in DAEMON mode (Continuous Loop Every {args.interval} Minutes)...")
        print(f"[TradingClaw] 데몬 모드 시작 (자동 사이클: {args.interval}분 마다)")
        while True:
            # Check market hours here if needed (could wrap run_cycle in a helper)
            await run_cycle(args.objective)
            print(f"[TradingClaw] ⏳ 다음 사이클까지 대기: {args.interval}분")
            await asyncio.sleep(args.interval * 60)
    else:
        await run_cycle(args.objective)
        print("[TradingClaw] 단발성 실행 완료.")

    emergency_task.cancel()
    try:
        await emergency_task
    except (asyncio.CancelledError, Exception):
        pass

    if bridge:
        try:
            await bridge.disconnect()
            print("[TradingClaw] MCP 브릿지 연결 해제")
        except Exception:
            pass  # anyio cancel scope 오류 억제


if __name__ == "__main__":
    import anyio
    anyio.run(main, backend="asyncio")

