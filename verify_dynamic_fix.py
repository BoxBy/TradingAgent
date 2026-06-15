
import os
import sys
import json
import asyncio

# Add project root to path
sys.path.append("/home/ubuntu/TradingAgent")

async def verify_system():
    print("--- Verifying Dynamic Strategy Loading ---")
    import config
    import importlib
    importlib.reload(config)
    print(f"Target Profit (config): {config.USER_RULES.get('target_profit_percent_per_trade')}%")
    print(f"Stop Loss (config): {config.USER_RULES.get('max_loss_percent_per_trade')}%")
    
    print("\n--- Verifying Exchange Rate Fallback ---")
    from mcp_bridge.kis_trading import KISOfficialClient
    client = KISOfficialClient()
    rate = client.get_exchange_rate()
    print(f"Retrieved Exchange Rate: {rate}")
    if rate != 1350.0:
        print("✅ SUCCESS: Real-time or fallback rate retrieved (not stuck at 1350.0)")
    else:
        print("⚠️ WARNING: Rate is 1350.0. This might be correct or fallback failed.")

    print("\n--- Verifying Prompt Placeholders ---")
    from teams import prompts
    if "{target_profit_pct}" in prompts.CLAW_CORE_INSTRUCTION:
        print("✅ SUCCESS: Prompts are using placeholders.")
    else:
        print("❌ FAILURE: Prompts still have hardcoded values.")

    print("\n--- Verifying Orchestrator Override Removal ---")
    with open("teams/orchestrator.py", "r") as f:
        content = f.read()
        if "100000000 # 100M KRW max" in content:
            print("❌ FAILURE: Hardcoded 100M KRW override still exists.")
        else:
            print("✅ SUCCESS: Hardcoded override removed.")

if __name__ == "__main__":
    asyncio.run(verify_system())
