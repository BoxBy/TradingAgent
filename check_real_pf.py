import json
import sys
from mcp_bridge.kis_trading import KISOfficialClient

def check_real_portfolio():
    try:
        kis = KISOfficialClient()
        portfolio = kis.get_portfolio()
        print(json.dumps(portfolio, indent=4, ensure_ascii=False))
    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    check_real_portfolio()
