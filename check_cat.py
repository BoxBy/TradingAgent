import sys
sys.path.insert(0, '.')
from mcp_bridge.kis_trading import KISOfficialClient
kis = KISOfficialClient()
portfolio = kis.get_portfolio()
print('Portfolio entries:')
for p in portfolio:
    code = p.get('stock_code', '?')
    qty = p.get('quantity', 0)
    pnl = p.get('pnl_percent', 0)
    mkt = p.get('market_type', '?')
    print(f"  {code} qty={qty} pnl={pnl:.2f}% market={mkt}")
if not portfolio:
    print("Empty portfolio returned!")
