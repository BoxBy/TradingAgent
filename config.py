import os
from dotenv import load_dotenv

# Path config
PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
ENV_PATH = os.path.join(PROJECT_ROOT, ".env")

# Load local environment
load_dotenv(dotenv_path=ENV_PATH)

def get_api_key(key_name: str) -> str:
    """Safely fetch an API key from the environment."""
    return os.getenv(key_name)

# --- Centralized Directories ---
DATA_DIR = os.path.join(PROJECT_ROOT, "data")
LOG_DIR = os.path.join(PROJECT_ROOT, "logs")
RAG_DB_PATH = os.path.join(PROJECT_ROOT, "rag_database")

os.makedirs(DATA_DIR, exist_ok=True)
os.makedirs(LOG_DIR, exist_ok=True)
os.makedirs(os.path.join(LOG_DIR, "charts"), exist_ok=True)
os.makedirs(RAG_DB_PATH, exist_ok=True)

# --- Core Trading Setup ---
TRADING_STYLE = os.getenv("TRADING_STYLE", "AGGRESSIVE")
MOCK_TRADING = os.getenv("MOCK_TRADING", "True").lower() == "true"
RUN_MODE = "TRADE"  # or "BACKTEST"

# --- User-defined Rules ---
CONSERVATIVE_RULES = {
    "max_loss_percent_per_trade": 15.0,
    "target_profit_percent_per_trade": 30.0,
    "max_investment_per_stock": 1000000,
    "portfolio_max_size": 10,
    "restricted_sectors": ["defense", "tobacco"],
    "min_cash_reserve_ratio": 0.4,
}

AGGRESSIVE_RULES = {
    "max_loss_percent_per_trade": 5.0,
    "target_profit_percent_per_trade": 3.0,
    "monthly_return_target_percent": 20.0,
    "max_investment_per_stock": 800000000,
    "portfolio_max_size": 15,
    "restricted_sectors": [],
    "min_cash_reserve_ratio": 0.10,
}

def load_dynamic_strategy():
    """Loads strategy from strategy.json, falling back to static rules."""
    import json
    strategy_path = os.path.join(DATA_DIR, "strategy.json")
    base_rules = AGGRESSIVE_RULES if TRADING_STYLE == "AGGRESSIVE" else CONSERVATIVE_RULES
    
    if os.path.exists(strategy_path):
        try:
            with open(strategy_path, "r") as f:
                dynamic = json.load(f)
                # Map strategy.json keys to rule keys
                mapping = {
                    "target_profit_pct": "target_profit_percent_per_trade",
                    "monthly_return_target_percent": "monthly_return_target_percent",
                    "stop_loss_pct": "max_loss_percent_per_trade",
                    "min_cash_reserve_ratio": "min_cash_reserve_ratio",
                    "max_portfolio_size": "portfolio_max_size",
                    "max_investment_per_stock_krw": "max_investment_per_stock"
                }
                for json_key, rule_key in mapping.items():
                    if json_key in dynamic:
                        base_rules[rule_key] = dynamic[json_key]
                return base_rules
        except Exception:
            return base_rules
    return base_rules

USER_RULES = load_dynamic_strategy()


def update_strategy_field(key: str, value) -> None:
    """Atomically update a single field in strategy.json (temp file + rename).

    Args:
        key: The JSON key to update (e.g. 'target_profit_pct', 'stop_loss_pct').
        value: The new value to write.
    """
    import json
    import tempfile

    strategy_path = os.path.join(DATA_DIR, "strategy.json")

    # Load existing strategy
    strategy = {}
    if os.path.exists(strategy_path):
        with open(strategy_path, "r") as f:
            strategy = json.load(f)

    strategy[key] = value

    # Atomic write: write to temp file in same dir, then os.replace (atomic on POSIX)
    dir_name = os.path.dirname(strategy_path)
    fd, tmp_path = tempfile.mkstemp(dir=dir_name, suffix=".tmp.json")
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(strategy, f, indent=2, ensure_ascii=False)
        os.replace(tmp_path, strategy_path)
    except Exception:
        # Clean up temp file on failure
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


# --- Transaction Costs ---
TRANSACTION_COSTS = {
    "KR": {
        "BUY_FEE": 0.0141639,
        "SELL_FEE": 0.0141639,
        "SELL_TAX": 0.18,
    },
    "US": {
        "BUY_FEE": 0.25,
        "SELL_FEE": 0.25,
        "SELL_TAX": 0.0,
    },
}

# --- Centralized Directories (MOVED UP) ---

# --- API Keys ---
FINNHUB_API_KEY = os.getenv("FINNHUB_API_KEY")
NAVER_CLIENT_ID = os.getenv("NAVER_CLIENT_ID")
NAVER_CLIENT_SECRET = os.getenv("NAVER_CLIENT_SECRET")
DEEPL_API_KEY = os.getenv("DEEPL_API_KEY")
SLACK_WEBHOOK_URL = os.getenv("SLACK_WEBHOOK_URL")
SLACK_BOT_TOKEN = os.getenv("SLACK_BOT_TOKEN")

# --- Notification Filtering ---
# Block list: categories here will NOT be sent to Slack.
# Available: "system", "cycle_report", "decomposition", "trade", "order_rejected", "emergency", "error_escalation"
NOTIFICATION_BLOCKED = ["decomposition", "order_rejected"]

# --- KIS Account settings ---
KIS_APP_KEY = os.getenv("KIS_MOCK_APP_KEY") if MOCK_TRADING else os.getenv("KIS_APP_KEY")
KIS_APP_SECRET = os.getenv("KIS_MOCK_APP_SECRET") if MOCK_TRADING else os.getenv("KIS_APP_SECRET")
KIS_ACCOUNT_NO = os.getenv("KIS_MOCK_ACCOUNT_NO") if MOCK_TRADING else os.getenv("KIS_ACCOUNT_NO")

# --- LLM Configuration (Centralized) ---
# 단일 위치에서 LLM Endpoint와 API Key를 관리합니다.
# 이 설정을 변경하면 전체 시스템에 적용됩니다.

# Gemini API Key Rotation Pool
GEMINI_API_KEYS = []
for i in range(1, 21):
    # .env uses GOOGLE_API_KEY_x format
    key = os.getenv(f"GOOGLE_API_KEY_{i}")
    if key:
        GEMINI_API_KEYS.append(key)

# If no indexed keys found, fallback to primary GEMINI_API_KEY or GOOGLE_API_KEY_1
if not GEMINI_API_KEYS:
    primary_gemini = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY_1")
    if primary_gemini:
        GEMINI_API_KEYS = [primary_gemini]

# LLM Provider Configuration
LLM_PROVIDER = "nvidia"  # Unified to NVIDIA NIM as primary
LLM_ENDPOINT = "http://localhost:4000"
LLM_API_KEY=os.getenv("LITELLM_MASTER_KEY")
LLM_MODEL = "ta-primary"

# Teammate (Gemini) Configuration - Fallback to Gemini 3.1
TEAMMATE_MODEL = "gemini-3.1-flash-lite"
TEAMMATE_ENDPOINT = "https://generativelanguage.googleapis.com/v1beta/openai/"

# Fallback LLM Chain: gemini-3.1-flash-lite → gemma-4-31b-it → gemma-4-26b-a4b-it
LLM_FALLBACK_ENDPOINT = "https://generativelanguage.googleapis.com/v1beta/openai/"

# Gemini API Key Rotation Pool for Fallback
GEMINI_FALLBACK_KEYS = []
for i in range(1, 21):
    key = os.getenv(f"GOOGLE_API_KEY_{i}")
    if key:
        GEMINI_FALLBACK_KEYS.append(key)

def get_fallback_config():
    """LiteLLM handles all routing. Minimal fallback config."""
    return {
        "model": "ta-primary",
        "base_url": "http://localhost:4000",
        "api_key": os.getenv("LITELLM_MASTER_KEY"),
        "next_fallback": None,  # LiteLLM handles fallbacks internally
    }


# LLM Retry Configuration
LLM_MAX_RETRIES = 5  # 최대 재시도 횟수
LLM_RETRY_DELAY_BASE = 2  # 기본 재시도 대기 시간 (초)
LLM_RETRY_DELAY_MAX = 60  # 최대 재시도 대기 시간 (초)
LLM_BACKOFF_MULTIPLIER = 2  # 지수 백오프 배수
