from typing import Tuple, List, Optional

# ETF 프리픽스 (한국)
ETF_PREFIXES_KR = [
    "KODEX", "TIGER", "KBSTAR", "ARIRANG",
    "HANARO", "TIMEFOLIO", "SOL", "KINDEX",
    "KOSEF", "TREX", "SMART"
]

# 주요 ETF 티커 (미국)
ETF_TICKERS_US = {
    "SPY", "VOO", "IVV", "QQQ", "DIA", "IWM", "VTI",
    "XLF", "XLK", "XLE", "XLV", "XLI", "XLP", "XLY", "XLU", "XLB", "XLRE",
    "ARKK", "ARKW", "ARKG", "SOXX", "IBB", "GDX", "TAN",
}

SECTOR_KEYWORDS = {
    "Tech": ["AI", "인공지능", "반도체", "소프트웨어", "클라우드", "Semiconductor", "Software", "Cloud"],
    "AI": ["AI", "인공지능", "딥러닝", "머신러닝", "ChatGPT", "NVIDIA"],
    "Battery": ["2차전지", "배터리", "전기차", "EV", "Battery"],
    "Bio": ["바이오", "제약", "신약", "Bio", "Pharma", "Healthcare"],
    "Finance": ["금융", "은행", "증권", "Finance", "Bank"],
    "Energy": ["에너지", "석유", "신재생", "Energy", "Oil", "Solar"],
    "Market": ["KOSPI", "S&P", "Nasdaq", "시장", "증시"],
}

ETF_SECTOR_MAP = {
    "AI": ["AI"], "2차전지": ["Battery"], "반도체": ["Tech"],
    "바이오": ["Bio"], "금융": ["Finance"], "에너지": ["Energy"],
    "200": ["Market"], "QQQ": ["Tech"], "SPY": ["Market"],
}

def is_etf(ticker: str, name: str = "") -> bool:
    for prefix in ETF_PREFIXES_KR:
        if name.startswith(prefix):
            return True
    return ticker.upper() in ETF_TICKERS_US

def get_asset_type(ticker: str, name: str = "") -> str:
    return "ETF" if is_etf(ticker, name) else "STOCK"

def get_etf_sector(etf_name: str) -> str:
    name_upper = etf_name.upper()
    for keyword, sectors in ETF_SECTOR_MAP.items():
        if keyword in name_upper:
            return sectors[0]
    if "AI" in name_upper: return "AI"
    elif "2차전지" in etf_name or "배터리" in etf_name: return "Battery"
    elif "반도체" in etf_name: return "Tech"
    elif "바이오" in etf_name: return "Bio"
    elif "200" in etf_name or "S&P" in name_upper: return "Market"
    return "General"

def get_sector_keywords(sector: str) -> List[str]:
    return SECTOR_KEYWORDS.get(sector, [])

def get_top_holdings(ticker: str, etf_name: str) -> List[Tuple[str, str, float]]:
    holdings_map = {
        "KODEX 200": [("005930", "삼성전자", 0.20), ("000660", "SK하이닉스", 0.08), ("005380", "현대차", 0.05)],
        "QQQ": [("AAPL", "Apple", 0.12), ("MSFT", "Microsoft", 0.10), ("NVDA", "NVIDIA", 0.07)],
        "SPY": [("AAPL", "Apple", 0.07), ("MSFT", "Microsoft", 0.06), ("NVDA", "NVIDIA", 0.05)],
    }
    for key, holdings in holdings_map.items():
        if key in etf_name or ticker.upper() == key:
            return holdings
    return []
