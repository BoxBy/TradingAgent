import json
import os
from datetime import datetime
import config
from core.monitor import get_system_logger

log = get_system_logger(__name__)

MARKET_REPORTS_FILE = os.path.join(config.LOG_DIR, "market_conditions_history.json")

def load_previous_market_reports(limit: int = 3) -> str:
    """최근 N개의 시장 위험 보고서를 로드하여 문자열로 반환합니다."""
    if not os.path.exists(MARKET_REPORTS_FILE):
        return "No previous reports available."
    try:
        with open(MARKET_REPORTS_FILE, "r") as f:
            reports = json.load(f)
        recent = reports[-limit:]
        lines = []
        for r in recent:
            ts = r.get("timestamp", "Unknown")
            vix = r.get("vix", "N/A")
            reasoning = r.get("reasoning", "N/A")
            lines.append(f"[{ts}] VIX: {vix} | Reason: {reasoning}")
        return "\n".join(lines)
    except Exception as e:
        log.error(f"Failed to load previous market reports: {e}")
        return "Error loading reports."

def save_market_report(report_data: dict):
    """새로운 시장 위험 보고서를 히스토리 파일에 저장합니다."""
    try:
        reports = []
        if os.path.exists(MARKET_REPORTS_FILE):
            with open(MARKET_REPORTS_FILE, "r") as f:
                try:
                    reports = json.load(f)
                except json.JSONDecodeError:
                    reports = []
        report_data["timestamp"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        reports.append(report_data)
        if len(reports) > 50:
            reports = reports[-50:]
        with open(MARKET_REPORTS_FILE, "w") as f:
            json.dump(reports, f, indent=2)
    except Exception as e:
        log.error(f"Failed to save market report: {e}")
