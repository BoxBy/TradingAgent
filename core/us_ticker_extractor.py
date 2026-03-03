import re
import json
from typing import Dict, List, Set, Optional

# Top 100+ US companies by market cap + common news mentions
US_COMPANY_TO_TICKER = {
    'Apple': 'AAPL', 'Microsoft': 'MSFT', 'Alphabet': 'GOOGL', 'Google': 'GOOGL',
    'Amazon': 'AMZN', 'Meta': 'META', 'Facebook': 'META', 'Tesla': 'TSLA',
    'Nvidia': 'NVDA', 'Netflix': 'NFLX', 'Adobe': 'ADBE', 'Salesforce': 'CRM',
    'Intel': 'INTC', 'AMD': 'AMD', 'Qualcomm': 'QCOM', 'Broadcom': 'AVGO',
    'Berkshire': 'BRK-B', 'JPMorgan': 'JPM', 'JP Morgan': 'JPM',
    'Visa': 'V', 'Mastercard': 'MA', 'Bank of America': 'BAC',
    'Walmart': 'WMT', 'Coca-Cola': 'KO', 'PepsiCo': 'PEP', 'Pepsi': 'PEP',
    'Target': 'TGT', 'Lowe': 'LOW', "Lowe's": 'LOW',
    'Disney': 'DIS', 'Comcast': 'CMCSA'
}

def extract_us_tickers_from_news(news_item: dict) -> dict:
    tickers = set()
    needs_llm = False
    
    headline = news_item.get("title", "")
    
    # Strategy 2: Extract from headline
    pattern_with_ticker = r'\(([A-Z]{1,5})\)'
    found = re.findall(pattern_with_ticker, headline)
    tickers.update(found)
    
    # Pattern 2: Company name mapping
    for company_name, ticker in US_COMPANY_TO_TICKER.items():
        if company_name in headline:
            tickers.add(ticker)
    
    # If no tickers found and headline mentions M&A keywords → Need LLM
    if not tickers and any(kw in headline.lower() for kw in 
                          ['acquisition', 'acquire', 'merger', 'buy', 'purchase', 'deal']):
        needs_llm = True
    
    return {'tickers': tickers, 'needs_llm': needs_llm}

def extract_us_tickers_batch(news_list: List[dict]) -> List[str]:
    all_tickers = set()
    for news in news_list:
        result = extract_us_tickers_from_news(news)
        all_tickers.update(result['tickers'])
    return sorted(list(all_tickers))
