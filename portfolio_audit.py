import json

portfolio = [
    {'stock_code': '000660', 'quantity': 10.0, 'current_price': 1103000.0, 'average_price': 941700.0, 'pnl_percent': 17.13, 'market_type': 'KR'},
    {'stock_code': '005930', 'quantity': 20.0, 'current_price': 206500.0, 'average_price': 190325.0, 'pnl_percent': 8.5, 'market_type': 'KR'},
    {'stock_code': 'AMD', 'quantity': 78.0, 'current_price': 255.07, 'average_price': 240.0, 'pnl_percent': 6.28, 'market_type': 'US'},
    {'stock_code': 'META', 'quantity': 28.0, 'current_price': 662.49, 'average_price': 627.362, 'pnl_percent': 5.6, 'market_type': 'US'},
    {'stock_code': 'NVDA', 'quantity': 170.0, 'current_price': 196.51, 'average_price': 183.095, 'pnl_percent': 7.33, 'market_type': 'US'},
    {'stock_code': 'PLTR', 'quantity': 50.0, 'current_price': 135.7, 'average_price': 145.88, 'pnl_percent': -6.98, 'market_type': 'US'}
]

cash_krw = 36774630
exchange_rate = 1400.0  # Assuming 1 USD = 1400 KRW

total_equity_krw = 0
total_unrealized_pnl_krw = 0
winners = []
losers = []

print(f"{'Ticker':<8} | {'Market':<4} | {'Qty':<6} | {'Price':<10} | {'PnL %':<8} | {'Unrealized PnL (KRW)':<20}")
print("-" * 75)

for stock in portfolio:
    ticker = stock['stock_code']
    qty = stock['quantity']
    curr_price = stock['current_price']
    avg_price = stock['average_price']
    pnl_pct = stock['pnl_percent']
    market = stock['market_type']
    
    if market == 'KR':
        market_value = qty * curr_price
        unrealized_pnl = (curr_price - avg_price) * qty
    else:
        market_value = qty * curr_price * exchange_rate
        unrealized_pnl = (curr_price - avg_price) * qty * exchange_rate
        
    total_equity_krw += market_value
    total_unrealized_pnl_krw += unrealized_pnl
    
    if pnl_pct >= 5.0:
        winners.append(ticker)
    elif pnl_pct <= -3.5:
        losers.append(ticker)
        
    print(f"{ticker:<8} | {market:<4} | {qty:<6} | {curr_price:<10.2f} | {pnl_pct:<8.2f} | {unrealized_pnl:<20.2f}")

total_portfolio_value = total_equity_krw + cash_krw

print("-" * 75)
print(f"Total Equity (KRW):         {total_equity_krw:,.2f}")
print(f"Total Unrealized PnL (KRW): {total_unrealized_pnl_krw:,.2f}")
print(f"Available Cash (KRW):       {cash_krw:,.2f}")
print(f"Total Portfolio Value (KRW): {total_portfolio_value:,.2f}")
print("-" * 75)
print(f"Winners for Profit-Taking: {winners}")
print(f"Losers for Stop-Loss:      {losers}")
