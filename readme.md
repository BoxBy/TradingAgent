## 📈 AI 기반 자동 주식 트레이딩 봇

안녕하세요\! 이 프로젝트는 최신 언어 모델(LLM)과 다양한 금융 데이터 API를 활용하여 주식 시장을 분석하고, 스스로 투자 결정을 내리는 AI 트레이딩 봇입니다. RAG(Retrieval-Augmented Generation) 기술을 통해 과거의 투자 경험을 학습하고, 시장 상황에 따라 동적으로 전략을 수정하며, 긴급 상황에 대응하는 기능까지 갖추고 있습니다.

-----

### ✨ 주요 기능

  * **🤖 다중 에이전트 시스템**: 기술적 분석, 감성 분석, 기본적 분석 등 각각의 역할을 수행하는 여러 AI 에이전트가 협력하여 종합적인 투자 결정을 내립니다.
  * **🧠 RAG 기반 학습 및 개선**: 과거의 성공 및 실패 거래 경험을 벡터 데이터베이스에 저장하고, 새로운 투자 결정을 내릴 때 유사한 사례를 참조하여 점차 똑똑해집니다.
  * **📰 동적 관심종목 생성**: 시장 뉴스를 실시간으로 분석하여 잠재력 있는 투자 대상을 동적으로 포착하고 관심종목 리스트를 생성합니다.
  * **🚨 시장 위험 감지 및 긴급 대응**: VIX 지수와 시장 뉴스를 모니터링하여 시장의 위험도를 판단하고, 위험 상황 발생 시 포트폴리오를 자동으로 청산하는 등 긴급 대응을 수행합니다.
  * **📊 백테스팅 기능**: 과거 데이터를 사용하여 개발된 투자 전략의 성과를 검증해볼 수 있습니다.
  * **🔔 실시간 알림**: 모든 거래 내역과 일일 계좌 현황, 주요 분석 결과를 슬랙(Slack)으로 전송받을 수 있습니다.

-----

### 🏗️ 시스템 아키텍처

이 트레이딩 봇은 다음과 같은 흐름으로 작동합니다.

1.  **뉴스 수집 (News Collection)**: 스케줄러가 주기적으로 Finnhub, Naver 등에서 최신 시장 및 종목 뉴스를 수집하여 로컬 데이터베이스에 저장합니다.
2.  **관심종목 생성 (Watchlist Generation)**: `NewsScreenerAgent`가 수집된 뉴스에서 언급된 종목들을 추출하여 동적인 관심종목 리스트를 만듭니다.
3.  **데이터 수집 (Data Ingestion)**: `DataIngestion` 모듈이 관심종목에 대한 최신 주가(OHLCV), 재무 데이터, 관련 뉴스 등을 수집합니다.
4.  **다중 에이전트 분석 (Multi-Agent Analysis)**:
      * `TechnicalAnalysisAgent`: 이동평균선, RSI 등 기술적 지표를 분석합니다.
      * `SentimentAnalysisAgent`: 뉴스의 긍정/부정 톤을 분석합니다.
      * `FundamentalAnalysisAgent`: 기업의 재무 건전성을 평가합니다.
      * `QualitativeAnalysisAgent`: 경영진, 경쟁 우위 등 정성적 요소를 평가합니다.
5.  **의사결정 오케스트레이터 (Trading Orchestrator)**:
      * 각 에이전트의 분석 결과를 종합합니다.
      * `RAGManager`를 통해 과거의 유사한 투자 성공/실패 사례(Insights)를 검색합니다.
      * 모든 정보를 바탕으로 최종적으로 '매수', '매도', '보유' 결정을 내립니다.
6.  **주문 실행 및 모니터링 (Order & Monitor)**:
      * `TradingInterface`를 통해 실제 증권사(한국투자증권) API로 주문을 전송합니다.
      * `TradeMonitor`가 매수한 종목의 목표가, 손절가, 매도 기한을 지속적으로 감시하고 조건 충족 시 자동으로 매도 주문을 실행합니다.
7.  **기록 및 알림 (Logging & Notification)**:
      * 모든 거래 내역과 일일 손익은 로그 파일로 기록됩니다.
      * 주요 이벤트와 리포트는 슬랙으로 실시간 알림이 전송됩니다.

-----

### 🚀 시작하기

#### 1\. 사전 준비

  * **Python**: 이 프로젝트는 **Python 3.10 이상** 버전이 필요합니다.
  * **Git**: 코드를 내려받기 위해 Git이 설치되어 있어야 합니다.

#### 2\. 프로젝트 클론 및 설정

터미널(명령 프롬프트)을 열고 다음 명령어를 순서대로 입력하세요.

```bash
# 1. 프로젝트 폴더로 진입합니다.
cd TradingAgent

# 2. 파이썬 가상환경을 생성하고 활성화합니다.
python -m venv venv
source venv/bin/activate  # macOS/Linux
# venv\Scripts\activate  # Windows

# 3. 필요한 라이브러리를 설치합니다.
pip install -r requirements.txt
```

#### 3\. API 키 발급 및 설정

이 봇을 실행하려면 여러 서비스의 API 키가 필요합니다. **매우 중요하니** 아래 가이드를 차근차근 따라주세요.

**1) `api.env` 파일 생성**

프로젝트의 최상위 폴더(`TradingAgent/`)에 `api.env` 라는 이름으로 새 파일을 만드세요. 그리고 아래 내용을 복사하여 붙여넣습니다. 이제 각 서비스에서 발급받은 키로 `"..."` 부분을 채워주시면 됩니다.

```env
# src/config.py 파일에서 이 키들을 불러와 사용합니다.

# 1. Google Gemini API Key (필수)
# 봇의 핵심 두뇌인 LLM 에이전트들이 사용합니다.
GOOGLE_API_KEY_1="..."
GOOGLE_API_KEY_2="..." # (선택사항) 여분 키가 있다면 추가

# 2. 한국투자증권 API Key (필수)
# 실제 주문 실행 및 계좌 정보 조회를 위해 필요합니다.
# 실전투자용
KIS_APP_KEY="..."
KIS_APP_SECRET="..."
KIS_ACCOUNT_NO="..." # 계좌번호-01 형식

# 모의투자용
KIS_MOCK_APP_KEY="..."
KIS_MOCK_APP_SECRET="..."
KIS_MOCK_ACCOUNT_NO="..." # 계좌번호-01 형식

# 3. Finnhub API Key (필수)
# 해외 주식의 뉴스 및 재무 데이터를 위해 필요합니다.
FINNHUB_API_KEY="..."

# 4. Naver Search API Key (필수)
# 국내 주식 뉴스 검색을 위해 필요합니다.
NAVER_CLIENT_ID="..."
NAVER_CLIENT_SECRET="..."

# 5. DeepL API Key (선택)
# 슬랙 알림 메시지를 한글로 번역할 때 사용됩니다.
DEEPL_API_KEY="..."

# 6. Slack API (선택)
# 봇의 활동을 실시간으로 알림 받기 위해 필요합니다.
SLACK_WEBHOOK_URL="..."
SLACK_BOT_TOKEN="..."
```

**2) 각 API 키 발급 방법**

  * **🤖 Google Gemini API 키 (필수)**

    1.  [Google AI Studio](https://aistudio.google.com/app/apikey)에 접속하여 구글 계정으로 로그인합니다.
    2.  `Create API key in new project` 버튼을 클릭하여 새 프로젝트를 만들고 API 키를 생성합니다.
    3.  생성된 키를 복사하여 `api.env` 파일의 `GOOGLE_API_KEY_1`에 붙여넣습니다.
    4.  (팁) Gemini는 무료 플랜에 분당 요청 수(RPM) 제한이 있습니다. 여러 개의 구글 계정으로 키를 여러 개 발급받아 `GOOGLE_API_KEY_2`, `GOOGLE_API_KEY_3` ... 에 추가하면, 하나의 키가 한도에 도달했을 때 봇이 자동으로 다음 키로 전환하여 사용합니다.

  * **💼 한국투자증권 API 키 (필수)**

    1.  [한국투자증권 API 개발자 포털](https://apiportal.koreainvestment.com/)에 접속하여 회원가입합니다.
    2.  **모의투자**와 **실전투자**는 별개의 절차입니다. 처음에는 **모의투자**로 시작하는 것을 강력히 권장합니다.
    3.  **(모의투자)**
          * 로그인 후, 상단 메뉴에서 `모의투자` -\> `모의투자 신청`을 클릭하여 모의투자 계좌를 개설합니다.
          * `서비스 신청` 메뉴로 이동하여 `API 서비스`를 신청합니다.
          * `개발가이드` -\> `앱 등록` 메뉴에서 새로운 애플리케이션을 등록합니다.
          * 등록이 완료되면 `App Key`와 `App Secret`이 발급됩니다. 이 값들을 복사하여 `api.env`의 `KIS_MOCK_APP_KEY`, `KIS_MOCK_APP_SECRET`에 붙여넣습니다.
          * `KIS_MOCK_ACCOUNT_NO`에는 발급받은 모의투자 계좌번호와 `-01`을 붙여 `12345678-01` 형식으로 입력합니다.
    4.  **(실전투자)**
          * 실제 한국투자증권 계좌가 필요합니다.
          * `서비스 신청` 메뉴에서 `실전투자` 탭을 선택하고 API 서비스를 신청합니다.
          * 모의투자와 마찬가지로 앱을 등록하고 `App Key`, `App Secret`을 발급받아 `api.env`에 입력합니다.
          * `KIS_ACCOUNT_NO`에 실제 계좌번호를 `-01`과 함께 입력합니다.

  * **📰 Finnhub API 키 (필수)**

    1.  [Finnhub.io](https://finnhub.io/)에 접속하여 `Get free API key` 버튼을 눌러 회원가입합니다.
    2.  로그인 후 대시보드에서 API 키를 확인할 수 있습니다.
    3.  이 키를 복사하여 `api.env` 파일의 `FINNHUB_API_KEY`에 붙여넣습니다.

  * **🔍 Naver Search API 키 (필수)**

    1.  [네이버 개발자 센터](https://developers.naver.com/)에 접속하여 로그인합니다.
    2.  상단 메뉴에서 `Application` -\> `애플리케이션 등록`을 선택합니다.
    3.  애플리케이션 이름(예: MyTradingBot)을 입력하고, `검색` API를 선택하여 등록합니다.
    4.  등록된 애플리케이션 정보에서 `Client ID`와 `Client Secret` 값을 확인합니다.
    5.  각 값을 복사하여 `api.env` 파일에 붙여넣습니다.

  * **🌐 DeepL API 키 (선택)**

    1.  [DeepL.com](https://www.deepl.com/pro-api)에 접속하여 API 플랜에 가입합니다. (무료 플랜도 제공됩니다.)
    2.  가입 후 계정 정보에서 `Authentication Key`를 확인합니다.
    3.  이 키를 복사하여 `api.env` 파일의 `DEEPL_API_KEY`에 붙여넣습니다.

  * **🔔 Slack API 설정 (선택)**

    1.  **Incoming Webhook (간단한 메시지 전송용)**
          * [Slack API](https://api.slack.com/apps) 페이지로 이동하여 `Create New App` -\> `From scratch`를 선택합니다.
          * 앱 이름(예: Trading Bot)을 정하고 워크스페이스를 선택합니다.
          * `Incoming Webhooks` 기능을 찾아 활성화(On)하고, `Add New Webhook to Workspace` 버튼을 눌러 메시지를 보낼 채널을 선택합니다.
          * 생성된 `Webhook URL`을 복사하여 `api.env`의 `SLACK_WEBHOOK_URL`에 붙여넣습니다.
    2.  **Bot Token (로그 파일 업로드용)**
          * 방금 만든 앱의 `OAuth & Permissions` 메뉴로 이동합니다.
          * `Bot Token Scopes` 섹션에서 `Add an OAuth Scope`를 클릭하고 `files:write` 권한을 추가합니다.
          * 페이지 상단에서 `Install to Workspace` 버튼을 눌러 앱을 재설치합니다.
          * 설치 후 나타나는 `Bot User OAuth Token` (xoxb- 로 시작)을 복사하여 `api.env`의 `SLACK_BOT_TOKEN`에 붙여넣습니다.
          * 알림을 보낼 슬랙 채널에 방금 만든 앱을 초대해야 합니다. (`/invite @앱이름`)

-----

### ⚙️ 주요 설정

`src/config.py` 파일에서 봇의 주요 동작을 설정할 수 있습니다.

  * **`MOCK_TRADING`**: `True`로 설정하면 모의투자 계좌로, `False`로 설정하면 실전투자 계좌로 작동합니다. **반드시 `True`로 시작하세요.**
  * **`TRADING_STYLE`**: "AGGRESSIVE"(공격형) 또는 "CONSERVATIVE"(안정형)를 선택할 수 있습니다. 각 스타일에 따른 상세 규칙(손절률, 목표 수익률 등)은 `AGGRESSIVE_RULES`, `CONSERVATIVE_RULES` 딕셔너리에서 수정할 수 있습니다.
  * **`RUN_MODE`**: `"TRADE"`로 설정하면 실시간 트레이딩 봇이, `"BACKTEST"`로 설정하면 백테스팅 모드가 실행됩니다.

-----

### ▶️ 실행 방법

1.  **가상환경 활성화**
    ```bash
    source venv/bin/activate  # macOS/Linux
    # venv\Scripts\activate  # Windows
    ```
2.  **초기 데이터 스크래핑 (필수)**
    봇을 처음 실행하기 전에, 필요한 종목 리스트 데이터를 미리 스크래핑해야 합니다. 다음 명령어를 실행하세요:
    ```bash
    python src/data_providers/scraper.py
    ```
    이 명령은 `data/kospi_tickers.csv`와 `data/nasdaq_tickers.csv` 파일을 생성합니다.


3.  **봇 실행**
    프로젝트 최상위 폴더(`TradingAgent/`)에서 다음 명령어를 실행합니다.
    ```bash
    python run_bot.py
    ```
    봇이 초기화되고, 스케줄에 따라 자동으로 뉴스 수집, 분석, 거래를 시작합니다. 슬랙 채널을 통해 봇의 활동을 실시간으로 확인할 수 있습니다.

-----

### 📁 프로젝트 구조

```
TradingAgent/
├── data/                 # KOSPI, NASDAQ 종목 리스트 등 데이터 파일 저장
├── logs/                 # 실행 로그, 거래 로그, 분석 차트 이미지 저장
│   ├── charts/
│   ├── reports/
│   └── ...
├── rag_database/         # 학습을 위한 RAG 벡터 데이터베이스 저장
├── src/                  # 핵심 소스 코드
│   ├── agents/           # 각 분석 역할을 담당하는 AI 에이전트
│   ├── core/             # 거래 모니터링, 포트폴리오 최적화 등 핵심 로직
│   ├── data_providers/   # 외부 API로부터 데이터를 가져오는 모듈
│   ├── emergency/        # 시장 위기 상황 감지 및 대응
│   ├── orchestrators/    # 에이전트들을 지휘하고 최종 결정을 내리는 모듈
│   ├── services/         # RAG, DB 관리 등 보조 서비스
│   └── utils/            # 로깅, 알림, API 키 관리 등 유틸리티
├── .gitignore            # Git 버전 관리에서 제외할 파일 및 폴더 목록
├── api.env               # (직접 생성) 모든 API 키를 저장하는 파일
├── requirements.txt      # 프로젝트 의존성 라이브러리 목록
└── run_bot.py            # 봇을 실행하는 메인 파일
```

-----

### ⚠️ 중요: 투자 유의사항

  * 이 프로젝트는 **학습 및 실험 목적으로 개발**되었습니다.
  * 자동화된 트레이딩 시스템은 항상 **예상치 못한 시장 변동성으로 인해 금전적 손실을 유발할 위험**이 있습니다.
  * 실전투자에 사용하기 전에 **반드시 모의투자를 통해 충분한 테스트**를 거치고, 코드의 모든 로직을 완벽하게 이해해야 합니다.
  * 이 프로젝트를 사용하여 발생하는 **모든 투자에 대한 책임은 사용자 본인**에게 있습니다.
