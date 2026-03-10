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
    "max_loss_percent_per_trade": 7.0,
    "target_profit_percent_per_trade": 1.5,
    "max_investment_per_stock": 200000000,
    "portfolio_max_size": 30,
    "restricted_sectors": [],
    "min_cash_reserve_ratio": 0.15,
}

USER_RULES = AGGRESSIVE_RULES if TRADING_STYLE == "AGGRESSIVE" else CONSERVATIVE_RULES

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

# --- Centralized Directories ---
DATA_DIR = os.path.join(PROJECT_ROOT, "data")
LOG_DIR = os.path.join(PROJECT_ROOT, "logs")
RAG_DB_PATH = os.path.join(PROJECT_ROOT, "rag_database")

os.makedirs(DATA_DIR, exist_ok=True)
os.makedirs(LOG_DIR, exist_ok=True)
os.makedirs(os.path.join(LOG_DIR, "charts"), exist_ok=True)
os.makedirs(RAG_DB_PATH, exist_ok=True)

# --- API Keys ---
FINNHUB_API_KEY = os.getenv("FINNHUB_API_KEY")
NAVER_CLIENT_ID = os.getenv("NAVER_CLIENT_ID")
NAVER_CLIENT_SECRET = os.getenv("NAVER_CLIENT_SECRET")
DEEPL_API_KEY = os.getenv("DEEPL_API_KEY")
SLACK_WEBHOOK_URL = os.getenv("SLACK_WEBHOOK_URL")
SLACK_BOT_TOKEN = os.getenv("SLACK_BOT_TOKEN")

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
LLM_PROVIDER = "nvidia"  # Orchestrator primary provider
LLM_ENDPOINT = os.getenv("LLM_ENDPOINT", "https://integrate.api.nvidia.com/v1")
LLM_API_KEY = os.getenv("NVIDIA_API_KEY", "")
LLM_MODEL = os.getenv("LLM_MODEL", "z-ai/glm5")

# Teammate (Gemini) Configuration
TEAMMATE_MODEL = "gemini-3.1-flash-lite-preview"
TEAMMATE_ENDPOINT = "https://generativelanguage.googleapis.com/v1beta/openai/"

# Fallback LLM Configuration (when primary GLM-5 fails)
LLM_FALLBACK_ENDPOINT = TEAMMATE_ENDPOINT
LLM_FALLBACK_API_KEY = GEMINI_API_KEYS[0] if GEMINI_API_KEYS else ""
LLM_FALLBACK_MODEL = TEAMMATE_MODEL
LLM_FALLBACK_KEYS = GEMINI_API_KEYS  # 폴백 시에도 키 로테이션 지원


# LLM Retry Configuration
LLM_MAX_RETRIES = 5  # 최대 재시도 횟수
LLM_RETRY_DELAY_BASE = 2  # 기본 재시도 대기 시간 (초)
LLM_RETRY_DELAY_MAX = 60  # 최대 재시도 대기 시간 (초)
LLM_BACKOFF_MULTIPLIER = 2  # 지수 백오프 배수
