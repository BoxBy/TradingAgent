import json

# Fix cached_balance.json
with open('logs/cached_balance.json') as f:
    data = json.load(f)
for p in data['last_get_portfolio']:
    if p.get('stock_code') == 'CAT':
        print(f'cached_balance Before: qty={p["quantity"]}')
        p['quantity'] = 5.0
        print(f'cached_balance After: qty={p["quantity"]}')
        break
with open('logs/cached_balance.json', 'w') as f:
    json.dump(data, f, indent=2)
print('cached_balance.json updated')

# Fix active_trades.json
with open('logs/active_trades.json') as f:
    data = json.load(f)
if 'CAT' in data:
    print(f'active_trades Before: qty={data["CAT"]["quantity"]}')
    data['CAT']['quantity'] = 5.0
    print(f'active_trades After: qty={data["CAT"]["quantity"]}')
with open('logs/active_trades.json', 'w') as f:
    json.dump(data, f, indent=4)
print('active_trades.json updated')
