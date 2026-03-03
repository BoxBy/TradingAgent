import json
import os
from datetime import datetime, timedelta

class ThresholdStore:
    """매매 임계값을 JSON 파일에 저장/로드하는 클래스."""
    FILE_PATH = "buy_threshold.json"

    def _load(self):
        try:
            if not os.path.exists(self.FILE_PATH):
                return {}
            with open(self.FILE_PATH, "r") as f:
                return json.load(f)
        except Exception:
            return {}

    def _save(self, data: dict):
        try:
            with open(self.FILE_PATH, "w") as f:
                json.dump(data, f, indent=2)
        except Exception:
            pass

    def set(self, market: str, value: float, confidence: float, ttl_minutes: int, reasoning: str):
        data = self._load()
        now = datetime.utcnow()
        expires_at = now + timedelta(minutes=int(ttl_minutes))
        data[market.upper()] = {
            "value": float(value),
            "confidence": float(confidence),
            "reasoning": reasoning or "",
            "created_at": now.isoformat(),
            "expires_at": expires_at.isoformat(),
        }
        self._save(data)

    def get(self, market: str):
        data = self._load()
        item = data.get(market.upper())
        if not item:
            return None
        try:
            exp = datetime.fromisoformat(item.get("expires_at"))
            if datetime.utcnow() > exp:
                return None
        except Exception:
            return None
        return item
