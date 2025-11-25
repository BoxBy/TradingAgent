import os

from dotenv import load_dotenv

# --- 경로 설정 수정 ---
# 1. config.py 파일이 있는 디렉토리 경로를 얻습니다. -> .../TradingAgent/src
SRC_DIR = os.path.dirname(os.path.abspath(__file__))

# 2. src 디렉토리의 상위 디렉토리(프로젝트 루트) 경로를 얻습니다. -> .../TradingAgent
PROJECT_ROOT = os.path.dirname(SRC_DIR)

# 3. 프로젝트 루트를 기준으로 api.env 파일의 절대 경로를 만듭니다.
dotenv_path = os.path.join(PROJECT_ROOT, "api.env")

# 4. 해당 경로의 .env 파일을 로드합니다.
load_dotenv(dotenv_path=dotenv_path)

# --- API Keys ---
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
GOOGLE_API_KEYS = [
    key for key in [os.getenv(f"GOOGLE_API_KEY_{i}") for i in range(1, 1000)] if key
]
FINNHUB_API_KEY = os.getenv("FINNHUB_API_KEY")

# --- (선택사항) 디버깅을 위한 print문 추가 ---
# 아래 print문을 추가하고 실행하면 경로가 올바르게 잡혔는지 확인할 수 있습니다.
print(f"Project Root: {PROJECT_ROOT}")
print(f"Loading .env from: {dotenv_path}")
print(f"GEMINI Key Loaded: {'Yes' if os.getenv('GOOGLE_API_KEY_1') else 'No'}")
# ---------------------------------------------

# --- KIS Real Trading Credentials ---
KIS_APP_KEY = os.getenv("KIS_APP_KEY")
KIS_APP_SECRET = os.getenv("KIS_APP_SECRET")
KIS_ACCOUNT_NO = os.getenv("KIS_ACCOUNT_NO")

# --- KIS Mock Trading Credentials ---
KIS_MOCK_APP_KEY = os.getenv("KIS_MOCK_APP_KEY")
KIS_MOCK_APP_SECRET = os.getenv("KIS_MOCK_APP_SECRET")
KIS_MOCK_ACCOUNT_NO = os.getenv("KIS_MOCK_ACCOUNT_NO")

# ✨ Slack Webhook URL 로드 추가 ✨
SLACK_WEBHOOK_URL = os.getenv("SLACK_WEBHOOK_URL")
SLACK_BOT_TOKEN = os.getenv("SLACK_BOT_TOKEN")

# ✨ Naver API 키 로드 추가 ✨
NAVER_CLIENT_ID = os.getenv("NAVER_CLIENT_ID")
NAVER_CLIENT_SECRET = os.getenv("NAVER_CLIENT_SECRET")

DEEPL_API_KEY = os.getenv("DEEPL_API_KEY")

# --- System Configuration ---
LLM_MODEL_NAME = "gemini-2.5-flash"
MOCK_TRADING = True

# 데이터, 로그, DB 저장 경로 (프로젝트 루트 기준)
PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
DATA_DIR = os.path.join(PROJECT_ROOT, "data")
LOG_DIR = os.path.join(PROJECT_ROOT, "logs")
RAG_DB_PATH = os.path.join(PROJECT_ROOT, "rag_database")

# --- ✨ 투자 스타일 선택 ✨ ---
# "CONSERVATIVE" (안정형) 또는 "AGGRESSIVE" (공격형) 중 선택
TRADING_STYLE = "AGGRESSIVE"
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

# 공격형 투자 규칙
AGGRESSIVE_RULES = {
    "max_loss_percent_per_trade": 7.0,  # 손절 라인을 더 짧게 설정
    "target_profit_percent_per_trade": 5.0,  # 수익 실현 목표를 낮춰 더 잦은 거래 유도
    "max_investment_per_stock": 200000000,  # 종목당 투자 금액 상향
    "portfolio_max_size": 30,  # 더 많은 종목에 동시 투자
    "restricted_sectors": [],  # 투자 제외 섹터 없음
    "min_cash_reserve_ratio": 0.2,
}

TRANSACTION_COSTS = {
    "KR": {
        "BUY_FEE": 0.0141639,  # 국내 주식 매수 수수료
        "SELL_FEE": 0.0141639,  # 국내 주식 매도 수수료
        "SELL_TAX": 0.18,  # 국내 주식 매도 세금 (2025년 기준)
    },
    "US": {
        "BUY_FEE": 0.25,  # 해외 주식(미국) 매수 수수료
        "SELL_FEE": 0.25,  # 해외 주식(미국) 매도 수수료
        "SELL_TAX": 0.0,  # 해외 주식 매도 세금 없음
    },
}

# 선택된 스타일에 따라 USER_RULES를 최종 결정
USER_RULES = AGGRESSIVE_RULES if TRADING_STYLE == "AGGRESSIVE" else CONSERVATIVE_RULES


# --- Directory Setup ---
os.makedirs(DATA_DIR, exist_ok=True)
os.makedirs(LOG_DIR, exist_ok=True)
os.makedirs(os.path.join(LOG_DIR, "charts"), exist_ok=True)
os.makedirs(RAG_DB_PATH, exist_ok=True)

# OPENAI_API_KEY	OpenAI GPT 모델 사용을 위한 API 키	sk-xxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
# FINNHUB_API_KEY	Finnhub 뉴스/데이터 API 사용을 위한 키	xxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
# KIS_APP_KEY	한국투자증권 실전투자 App Key	PSEDxxxxxxxxxxxxxxxxxxxxxxxx
# KIS_APP_SECRET	한국투자증권 실전투자 App Secret	xxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
# KIS_ACCOUNT_NO	한국투자증권 실전투자 계좌번호 (하이픈 포함)	00000000-01
# KIS_MOCK_APP_KEY	한국투자증권 모의투자 App Key	PSEDxxxxxxxxxxxxxxxxxxxxxxxx
# KIS_MOCK_APP_SECRET	한국투자증권 모의투자 App Secret	xxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
# KIS_MOCK_ACCOUNT_NO	한국투자증권 모의투자 계좌번호 (하이픈 포함)	00000000-01
