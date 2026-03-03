"""
Historical Threshold Analyzer
과거 거래 성과를 분석하여 최적 threshold를 추천하는 유틸리티.
"""
import re
import os
import json
from datetime import datetime, timedelta
from typing import List, Dict
import pandas as pd
import config
from core.monitor import get_system_logger

log = get_system_logger(__name__)

def collect_historical_performance(days: int = 45) -> List[Dict]:
    """과거 N일간의 거래 성과와 threshold 데이터 수집."""
    threshold_data = _extract_thresholds_and_vix_from_logs(days)
    trade_data = _extract_trade_performance_from_csv(days)
    return _merge_data(threshold_data, trade_data, days)

def _extract_thresholds_and_vix_from_logs(days: int) -> Dict:
    """logs/trading_agent.log에서 threshold와 VIX 추출."""
    log_path = os.path.join(config.LOG_DIR, "trading_agent.log")
    if not os.path.exists(log_path):
        return {}
    
    result = {}
    cutoff = datetime.now() - timedelta(days=days)
    
    try:
        with open(log_path, 'r', encoding='utf-8') as f:
            for line in f:
                try:
                    ts_match = re.match(r'(\d{4}-\d{2}-\d{2})', line)
                    if not ts_match:
                        continue
                    date_str = ts_match.group(1)
                    dt = datetime.strptime(date_str, "%Y-%m-%d")
                    if dt < cutoff:
                        continue
                    
                    vix_match = re.search(r'VIX[:\s]+(\d+\.?\d*)', line)
                    buy_match = re.search(r'buy.*threshold[:\s]+(\d+\.?\d*)', line, re.IGNORECASE)
                    sell_match = re.search(r'sell.*threshold[:\s]+-?(\d+\.?\d*)', line, re.IGNORECASE)
                    
                    if date_str not in result:
                        result[date_str] = {}
                    if vix_match:
                        result[date_str]['vix'] = float(vix_match.group(1))
                    if buy_match:
                        result[date_str]['buy_threshold'] = float(buy_match.group(1))
                    if sell_match:
                        result[date_str]['sell_threshold'] = -float(sell_match.group(1))
                except Exception:
                    continue
    except Exception as e:
        log.error(f"Error parsing logs: {e}")
    return result

def _extract_trade_performance_from_csv(days: int) -> Dict:
    """logs/trades_log.csv에서 거래 성과 추출."""
    csv_path = os.path.join(config.LOG_DIR, "trades_log.csv")
    if not os.path.exists(csv_path):
        return {}
    
    result = {}
    cutoff = datetime.now() - timedelta(days=days)
    
    try:
        df = pd.read_csv(csv_path)
        df['Timestamp'] = pd.to_datetime(df['Timestamp'])
        df = df[df['Timestamp'] >= cutoff]
        
        for date, group in df.groupby(df['Timestamp'].dt.date):
            date_str = str(date)
            buys = len(group[group['Action'] == 'BUY'])
            sells = len(group[group['Action'] == 'SELL'])
            result[date_str] = {
                'trades_count': len(group),
                'buys': buys,
                'sells': sells,
            }
    except Exception as e:
        log.error(f"Error parsing trades CSV: {e}")
    return result

def _merge_data(threshold_data: Dict, trade_data: Dict, days: int) -> List[Dict]:
    """Threshold/VIX 데이터와 거래 성과 데이터 병합."""
    all_dates = sorted(set(list(threshold_data.keys()) + list(trade_data.keys())))
    result = []
    for date_str in all_dates:
        entry = {"date": date_str}
        entry.update(threshold_data.get(date_str, {}))
        entry.update(trade_data.get(date_str, {}))
        result.append(entry)
    return result

def generate_performance_table(historical_data: List[Dict], max_rows: int = 30) -> str:
    """Historical data를 Markdown 테이블로 변환."""
    if not historical_data:
        return "No historical data available."
    
    data = historical_data[-max_rows:]
    lines = ["| Date | VIX | Buy Thresh | Sell Thresh | Trades |",
             "|------|-----|-----------|------------|--------|"]
    for d in data:
        vix = d.get('vix', 'N/A')
        bt = d.get('buy_threshold', 'N/A')
        st = d.get('sell_threshold', 'N/A')
        tc = d.get('trades_count', 0)
        lines.append(f"| {d['date']} | {vix} | {bt} | {st} | {tc} |")
    return "\n".join(lines)

def get_threshold_recommendation(historical_data: List[Dict], current_vix: float) -> Dict:
    """현재 VIX와 유사한 과거 조건에서 최적 threshold 찾기."""
    if not historical_data:
        return {"recommended_buy": 5.0, "recommended_sell": -6.5, "confidence": 0.5, "reasoning": "No historical data."}
    
    similar = [d for d in historical_data if 'vix' in d and abs(d['vix'] - current_vix) < 5]
    if not similar:
        return {"recommended_buy": 5.0, "recommended_sell": -6.5, "confidence": 0.3, "reasoning": "No similar VIX conditions found."}
    
    buy_thresholds = [d['buy_threshold'] for d in similar if 'buy_threshold' in d]
    sell_thresholds = [d['sell_threshold'] for d in similar if 'sell_threshold' in d]
    
    rec_buy = sum(buy_thresholds) / len(buy_thresholds) if buy_thresholds else 5.0
    rec_sell = sum(sell_thresholds) / len(sell_thresholds) if sell_thresholds else -6.5
    
    return {
        "recommended_buy": round(rec_buy, 1),
        "recommended_sell": round(rec_sell, 1),
        "confidence": min(0.9, len(similar) / 10),
        "reasoning": f"Based on {len(similar)} days with similar VIX (~{current_vix:.1f})"
    }
