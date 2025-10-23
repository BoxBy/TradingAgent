#### This repository was developed leveraging Gemini.

---

# TradingAgents: LLM 기반 자동 주식 거래 봇

## 📖 개요

**TradingAgents**는 최신 AI 기술인 대규모 언어 모델(LLM)을 활용하여 주식 시장 데이터를 분석하고, 투자 결정을 내리며, 거래를 자동으로 실행하는 파이썬 기반의 자동화된 트레이딩 봇 프로젝트입니다.

이 시스템은 뉴스, 재무 데이터, 기술적 지표 등 방대한 정보를 실시간으로 수집하고, 여러 전문 에이전트(Agent)가 협력하여 사람처럼 종합적으로 사고하여 투자 전략을 수립합니다. 또한, 과거의 투자 경험을 학습하여 스스로 전략을 개선해 나가는 RAG(Retrieval-Augmented Generation) 기술이 적용되어 있습니다.

## ✨ 주요 기능

  * **🤖 다중 에이전트 시스템**: 시장 분석, 뉴스 스크리닝, 종목 분석, 포트폴리오 관리 등 각기 다른 역할을 수행하는 다수의 LLM 에이전트가 유기적으로 협력하여 최적의 투자 결정을 내립니다.
  * **📈 실시간 데이터 분석**: Finnhub, yfinance, Naver 등 다양한 소스로부터 최신 시장 뉴스, 주가(OHLCV), 재무 데이터를 실시간으로 수집하고 분석합니다.
  * **🧠 RAG 기반 자가 학습**: ChromaDB 벡터 데이터베이스를 활용하여 과거의 모든 분석 내용과 투자 결정, 그 결과를 기록하고 학습합니다. 이를 통해 새로운 투자 결정 시 과거의 성공/실패 경험을 참조하여 더 나은 판단을 내립니다.
  * **🛡️ 중앙화된 프롬프트 관리**: 모든 LLM 에이전트의 행동을 결정하는 프롬프트가 `src/prompts.py` 파일에 중앙화되어 있어 전략 수정 및 관리가 용이합니다. (현재 공개된 버전은 핵심 로직을 제외하고 단순화되었습니다.)
  * **🚨 24시간 위험 관리**: VIX 지수 급등과 같은 시장 전반의 위기 상황이나 개별 종목에 대한 심각한 악재를 24시간 감시하고, 설정된 기준에 따라 보유 종목을 자동으로 매도하는 등 신속하게 위험에 대응합니다.
  * **⚙️ 동적 투자 전략**: 시장 상황(안정, 불안정 등)을 LLM이 종합적으로 판단하여, 보수적 또는 공격적으로 투자 기준(확신 점수)을 동적으로 변경합니다.
  * **📊 자동 리포팅 및 모니터링**: 매일 장 마감 후 계좌 현황, 거래 내역, 손익을 요약하여 슬랙(Slack)으로 리포트를 전송하고, 차트를 이미지로 저장하여 투자 과정을 투명하게 관리합니다.

## 🏗️ 시스템 아키텍처

TradingAgents는 다음과 같은 단계로 작동하는 정교한 시스템입니다.

1.  **데이터 수집 (Data Ingestion)**: `DataIngestion` 모듈이 Finnhub, Naver 등에서 시장 뉴스 및 종목 데이터를 수집합니다.
2.  **뉴스 스크리닝 (News Screening)**: `NewsScreenerAgent`가 새로운 뉴스들을 분석하여 투자 기회가 있을 만한 종목으로 구성된 **관심 종목(Watchlist)**을 동적으로 생성합니다.
3.  **심층 분석 (In-depth Analysis)**: `TradingOrchestrator`의 지휘 아래, 각 종목별로 여러 전문 에이전트가 동시에 분석을 수행합니다.
      * `TechnicalAnalysisAgent`: 이동평균선, RSI 등 기술적 지표를 분석합니다.
      * `FundamentalAnalysisAgent`: 재무제표를 바탕으로 기업의 펀더멘털을 평가합니다.
      * `SentimentAnalysisAgent`: 뉴스의 긍정/부정 톤을 분석하여 시장 감성을 파악합니다.
      * `QualitativeAnalysisAgent`: 기업의 비즈니스 모델, 경쟁 환경 등 정성적 요소를 평가합니다.
4.  **의사 결정 (Decision Making)**: `TradingOrchestrator`는 모든 에이전트의 분석 결과를 종합하고, RAG 시스템을 통해 과거의 관련 투자 기록과 비교하여 최종적으로 각 종목에 대한 **매수/매도/보류 결정**과 **확신 점수(Conviction Score)**를 매깁니다.
5.  **포트폴리오 최적화 (Portfolio Optimization)**: 여러 종목에 대한 매수 결정이 내려지면, `PortfolioOptimizer`가 현대 포트폴리오 이론(MPT)을 기반으로 위험 대비 수익률이 가장 높은 최적의 투자 비중을 계산합니다.
6.  **주문 실행 (Order Execution)**: 계산된 비중에 따라 `kis_wrapper`를 통해 한국투자증권(KIS) API로 실제 매수/매도 주문을 전송합니다.
7.  **모니터링 및 리포팅 (Monitoring & Reporting)**: `TradeMonitor`가 매수된 종목의 목표가 및 손절가를 지속적으로 추적하며, `reporting` 유틸리티는 일일 성과를 슬랙으로 보고합니다.

## 📂 프로젝트 구조

```
TradingAgents/
├── data/                    # 데이터 파일 저장 경로 (뉴스 DB 등)
├── logs/                    # 로그, 리포트, 차트 이미지 저장 경로
├── rag_database/            # RAG 학습 데이터 저장 경로
├── src/
│   ├── agents/              # 분석 역할을 담당하는 에이전트
│   │   ├── market.py        # 시장 전반 분석 에이전트
│   │   └── stock.py         # 개별 주식 분석 에이전트
│   ├── core/                # 핵심 로직 (모니터링, 최적화, 백테스팅)
│   │   ├── monitor.py       # 매수/매도 조건 모니터링
│   │   └── optimizer.py     # 포트폴리오 최적화
│   ├── data_providers/      # 외부 데이터 연동
│   │   ├── ingestion.py     # 데이터 수집
│   │   └── kis_wrapper.py   # 한국투자증권 API 래퍼
│   ├── emergency/           # 위기 상황 관리
│   │   └── manager.py       # 긴급 매도 등 위기 대응
│   ├── orchestrators/       # 에이전트 조율 및 의사결정 총괄
│   │   └── main.py          # 메인 오케스트레이터
│   ├── services/            # RAG 데이터베이스 관리
│   │   └── rag_manager.py     # RAG 벡터 DB 관리
│   ├── utils/               # 보조 기능 모듈
│   │   ├── api_key_manager.py # API 키 관리
│   │   ├── logger.py        # 로깅 설정
│   │   └── notification.py  # 슬랙 알림
│   └── prompts.py           # 모든 LLM 프롬프트 중앙 관리
├── api.env                  # API 키 및 계정 정보 설정 파일
├── requirements.txt         # 프로젝트 의존성 라이브러리 목록
└── run_bot.py               # 봇 실행 스크립트
```

## 🚀 시작하기

### 1\. 사전 준비

  * Python 3.9 이상
  * 필요한 API 키 발급
      * **Google AI (Gemini)**: LLM 모델 사용
      * **한국투자증권(KIS)**: 실거래/모의투자
      * **Finnhub**: 해외 주식 데이터 및 뉴스
      * **Slack**: 리포트 수신을 위한 Webhook URL
      * **DeepL**: (선택) 뉴스 번역 기능 사용 시

### 2\. 설치

1.  **프로젝트 클론 또는 다운로드**

2.  **필요 라이브러리 설치**

    ```bash
    pip install -r requirements.txt
    ```
    pykis 오류가 발생한다면 [pykis](https://github.com/BoxBy/pykis)를 설치해보시는것을 추천드립니다

3.  **API 키 설정**

      * 프로젝트 최상위 경로에 있는 `api.env.example` 파일의 이름을 `api.env`로 변경하세요.
      * `api.env` 파일을 열어 발급받은 각 API 키와 계좌 정보를 입력하세요.

    ```dotenv
    # api.env
    GOOGLE_API_KEY_1="AIza..."
    GOOGLE_API_KEY_2="AIza..."
    # ... (여러 개 입력 가능)

    KIS_APP_KEY="P..."
    KIS_APP_SECRET="..."
    KIS_ACCOUNT_NO="12345678-01" # 하이픈 포함

    # 모의투자 계좌 정보
    KIS_MOCK_APP_KEY="..."
    KIS_MOCK_APP_SECRET="..."
    KIS_MOCK_ACCOUNT_NO="12345678-01"

    FINNHUB_API_KEY="..."
    SLACK_WEBHOOK_URL="https://hooks.slack.com/services/..."
    DEEPL_API_KEY="..."
    ```

### 3\. 투자 스타일 설정

`src/config.py` 파일에서 `TRADING_STYLE` 변수를 `"AGGRESSIVE"`(공격형) 또는 `"CONSERVATIVE"`(안정형)으로 설정하여 봇의 투자 성향을 조절할 수 있습니다.

### 4\. 프롬프트 수정 및 전략 커스터마이징

이 프로젝트의 핵심은 `src/prompts.py` 파일에 정의된 LLM 프롬프트에 있습니다. 각 에이전트의 행동 방식, 분석의 깊이, 결정의 기준은 모두 이 프롬프트에 의해 결정됩니다. 자신만의 투자 전략을 구현하고 싶다면 이 파일을 수정하면 됩니다.

**수정 방법:**

1.  `src/prompts.py` 파일을 엽니다.
2.  수정하고 싶은 에이전트의 프롬프트 변수(예: `SENTIMENT_ANALYSIS_PROMPT`)를 찾습니다.
3.  원하는 대로 지시사항을 수정합니다. 중괄호로 묶인 변수명(예: `{news_headlines}`)은 코드에서 동적으로 채워지므로 반드시 유지해야 합니다.

**예시: 감성 분석 에이전트의 프롬프트 변경**

*   **기본 프롬프트:**
    ```python
    SENTIMENT_ANALYSIS_PROMPT = """Analyze the sentiment of these news headlines: {news_headlines}. Respond in JSON with 'sentiment' and 'summary' keys."""
    ```

*   **수정된 프롬프트 (더 구체적인 지시):**
    ```python
    SENTIMENT_ANALYSIS_PROMPT = """You are a cautious financial analyst specializing in the Korean market. Analyze these news headlines: {news_headlines}. Provide a sentiment score from -1.0 (very negative) to 1.0 (very positive). Also, identify the top 3 keywords. Respond ONLY in JSON format with keys 'sentiment_score', 'summary', and 'keywords'."""
    ```

> **⚠️ 주의**: 프롬프트를 변경하면 봇의 투자 결정과 성능에 큰 영향을 미칩니다. 변경 후에는 모의 투자를 통해 충분히 테스트하는 것을 권장합니다.

## 💡 사용법

프로젝트 최상위 디렉토리에서 아래 명령어를 실행하여 봇을 시작합니다.

```bash
python run_bot.py
```

봇은 `run_bot.py`에 정의된 스케줄에 따라 자동으로 데이터 수집, 분석, 거래, 리포팅 작업을 수행합니다.

## 📄 주요 파일 설명

  * **`run_bot.py`**: 스케줄링 라이브러리를 사용하여 정해진 시간마다 각 기능(뉴스 수집, 투자 결정, 모니터링 등)을 실행하는 메인 파일입니다.
  * **`src/prompts.py`**: 시스템의 모든 LLM 에이전트가 사용하는 프롬프트를 중앙에서 관리하는 파일입니다. 공개 버전에서는 상세한 투자 전략이 단순화되었습니다.
  * **`src/orchestrators/main.py`**: 각 전문 에이전트들의 분석 결과를 취합하고, 포트폴리오 최적화를 거쳐 최종 투자 결정을 내리는 프로젝트의 "두뇌"와 같은 역할을 합니다.
  * **`src/agents/market.py`**: `src/prompts.py`의 프롬프트를 기반으로 시장의 전반적인 상황을 분석합니다. 뉴스를 분석해 관심 종목을 찾아내고, 시장 위험도를 판단해 투자 기준을 동적으로 조절합니다.
  * **`src/agents/stock.py`**: `src/prompts.py`의 프롬프트를 기반으로 개별 종목에 대한 심층 분석을 수행합니다. 기술적, 기본적, 정성적, 감성 분석 등 다양한 관점에서 주식을 평가합니다.
  * **`src/core/monitor.py`**: 매수한 종목의 주가를 추적하며 목표가 도달 시 자동 익절, 손절가 도달 시 자동 손절매를 실행하는 등 거래 후 관리를 담당합니다.
  * **`src/services/rag_manager.py`**: 모든 분석과 거래 기록을 벡터 데이터베이스에 저장하고, 유사한 상황이 발생했을 때 관련 기록을 검색하여 LLM의 판단을 돕는 '장기 기억 장치' 역할을 합니다.
  * **`src/emergency/manager.py`**: VIX 지수 급등이나 개별 종목의 돌발 악재를 감지하여 포트폴리오를 긴급 청산하는 등 위험 관리 시스템의 핵심입니다.

## ⚠️ 면책 조항

본 프로젝트는 LLM을 활용한 자동 주식 거래 시스템의 구현 예시이며, 학습 및 연구 목적으로 제작되었습니다. 실제 투자에 사용될 경우 금전적 손실이 발생할 수 있습니다. 모든 투자에 대한 책임은 투자자 본인에게 있습니다.

-----