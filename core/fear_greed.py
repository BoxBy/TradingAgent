import yfinance as yf
from typing import Dict

def calculate_fear_greed_index(vix: float, sp500_change_5d: float = None) -> Dict:
    if vix <= 10: vix_score = 100
    elif vix >= 40: vix_score = 0
    else: vix_score = 100 - ((vix - 10) / 30) * 100
    
    momentum_score = 50
    if sp500_change_5d is not None:
        momentum_score = 50 + (sp500_change_5d * 10)
        momentum_score = max(0, min(100, momentum_score))
        
    index = vix_score * 0.7 + momentum_score * 0.3 if sp500_change_5d is not None else vix_score
    
    if index >= 75: level = "Extreme Greed"
    elif index >= 55: level = "Greed"
    elif index >= 45: level = "Neutral"
    elif index >= 25: level = "Fear"
    else: level = "Extreme Fear"
    
    return {'index': round(index, 1), 'level': level, 'vix_score': round(vix_score, 1), 'momentum_score': momentum_score}

def get_fear_greed_with_momentum(vix: float) -> Dict:
    try:
        sp500 = yf.Ticker("^GSPC")
        hist = sp500.history(period="10d")
        if len(hist) >= 6:
            change_pct = ((hist['Close'].iloc[-1] - hist['Close'].iloc[-6]) / hist['Close'].iloc[-6]) * 100
            return calculate_fear_greed_index(vix, change_pct)
    except:
        pass
    return calculate_fear_greed_index(vix)
