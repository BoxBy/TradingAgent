# TradingClaw

AI 기반 자율 트레이딩 시스템 - 한국투자증권(KIS) API 연동 및 다중 에이전트 아키텍처

## 특징

- **KR/US 이중 시장 지원**: 국내 주식 및 미국 주식 자동 매매
- **KIS API 연동**: 한국투자증권 실전/모의투자 API 지원 (1 req/sec rate limiter)
- **MCP Bridge 아키텍처**: Model Context Protocol 기반 API 샌드박싱
- **다중 에이전트 시스템**: Orchestrator + Teammate + Expert 협력 아키텍처
- **자동 리포트**: Slack/Discord 알림 및 자산 현황 리포트

## 시스템 구조

```
main.py                      # 메인 실행 진입점
├── OrchestratorAgent        # 목표 분할 및 작업 관리
├── TeammateAgent (N개)      # 병렬 태스크 수행
├── Expert Agents            # 기술적/펀더멘털 분석
└── EmergencyAgent           # 백그라운드 리스크 모니터링

mcp_bridge/
├── server_kis.py            # KIS API MCP 서버
├── client.py                # MCP 클라이언트
└── kis_trading.py           # KIS API 래퍼 (rate limiter 포함)

core/
├── reporting.py             # 슬랙 리포트 생성
├── tools.py                 # 계좌 조회/매매 도구
└── ...                      # 기반 유틸리티
```

## 시작하기

### 1. 환경 설정

```bash
# 의존성 설치
pip install -r requirements.txt

# .env 파일 설정
KIS_MOCK_APP_KEY=your_key
KIS_MOCK_APP_SECRET=your_secret
KIS_MOCK_ACCOUNT_NO=12345678-01
SLACK_WEBHOOK_URL=your_webhook_url
```

### 2. 실행

```bash
# 단발성 실행
python3 main.py --objective "시장 스캔 및 매매 기회 분석"

# 데몬 모드 (15분 간격)
python3 main.py --daemon --interval 15
```

## 잔고 리포트 예시

```
📦 계좌 현황 리포트
💰 총 자산: ₩436,007,866
🇰🇷 국내: ₩98,128,857 (현금: ₩98,128,857)
🇺🇸 해외: ₩337,879,009 (포트폴리오+현금, 환율: 1,350.0)
```

## API 제한 사항

- **Mock 모드**: 1 req/sec
- **실전 모드**: 19 req/sec (권장)
- 모든 API 호출에 재시도 로직 및 캐싱 fallback 포함

## 라이선스

MIT License
