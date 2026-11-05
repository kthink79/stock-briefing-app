# 📈 stock-briefing-app

매일 사용자가 지정한 시간 및 주기적 간격으로 국내외 증시 및 반도체·우주항공 기술주 중심의 투자 정보를 수집하고 분석하여 리포트해 주는 서비스 & 안드로이드 애플리케이션입니다.  
**Gemini**의 심층 분석과 **Google Jules**를 통한 지속적인 코드 검증 및 개선 파이프라인을 지원합니다.

---

## 🚀 주요 기능

1. **글로벌 증시 & 거시경제 지표 수집**
   - 미국 3대 지수(다우, 나스닥, S&P 500) 및 필라델피아 반도체 지수(SOX) 마감 지표
   - 원/달러 환율, 미 국채 10년물 금리 등 거시경제 핵심 지표
2. **핵심 섹터 심층 모니터링**
   - **반도체**: 삼성전자, SK하이닉스, 엔비디아, TSMC, ASML 등 글로벌 공급망 동향
   - **우주항공**: 한화에어로스페이스, KAI, SpaceX, 미 방산 및 우주 탐사 주요 이슈
3. **기업 실적 & 펀더멘털 밸류에이션 분석 (`company_analyzer`)**
   - 주가, PER, PBR, EPS, BPS, 시가총액, 배당수익률 등 기업 밸류에이션 핵심 지표 산출
   - 최근 분기 실적(매출/영업이익 성장률) 및 어닝 서프라이즈/쇼크 분석
   - 실적 관련 핵심 뉴스 수집 및 Gemini 기반의 적정가치·펀더멘털 총평 리포트 제공
4. **사용자 맞춤형 리포트 시간 & 주기적 간격(Interval) 업데이트 (`scheduler_engine`)**
   - 기본 08:00 AM 외에 사용자가 원하는 시각 다중 지정 가능
   - 장중(09:00~15:30) 또는 설정한 간격(예: 1시간, 3시간 단위)으로 실시간 뉴스/지표 변동 자동 갱신
5. **키워드 & 주제 트레이스 타임라인 관리 (`trace_engine`)**
   - 관심 키워드(예: '트럼프') 또는 특정 주제(예: '미국 이란 전쟁')를 트레이스로 등록
   - 등록 시점부터 발생하는 관련 발언, 사건, 정책 뉴스를 실시간 매칭하여 타임라인에 누적
   - Gemini 기반의 누적 이력 롤링 요약(Rolling Summary) 자동 갱신
6. **Google Jules 기반 지속적 검증 & 최적화**
   - GitHub 연동을 통해 테스트 코드 자동 작성, 크롤러 예외 처리 강화, 성능 개선 PR 생성 및 자동 반영

---

## 📂 프로젝트 구조

```
stock-briefing-app/
├── crawler/                  # 데이터 수집 및 분석 모듈 (Python)
│   ├── crawler.py           # 증시 지표 및 기술주 뉴스 크롤러 (Jules PR #1 반영)
│   ├── company_analyzer.py  # 기업 실적, PER, PBR, EPS 밸류에이션 분석 엔진
│   ├── gemini_analyzer.py   # Gemini 프롬프트 및 분석 엔진
│   ├── scheduler_engine.py  # 리포트 시각 및 주기적 간격 스케줄러 (Jules PR #2 반영)
│   ├── trace_engine.py      # 키워드/주제 트레이스 및 타임라인 엔진 (Jules PR #2 반영)
│   ├── requirements.txt     # 의존성 패키지
│   └── tests/
│       ├── test_crawler.py              # 크롤러 단위 테스트
│       ├── test_trace_and_scheduler.py  # 트레이스 및 스케줄러 단위 테스트
│       └── test_company_analyzer.py     # 기업 밸류에이션 단위 테스트
├── android/                  # 안드로이드 애플리케이션 (Kotlin/Compose)
│   ├── app/
│   │   ├── src/main/java/com/kthink79/stockbriefing/
│   │   │   ├── MainActivity.kt
│   │   │   ├── model/
│   │   │   │   ├── Report.kt
│   │   │   │   ├── CompanyModels.kt
│   │   │   │   └── TraceModels.kt
│   │   │   └── notification/AlarmScheduler.kt
│   │   └── AndroidManifest.xml
│   └── build.gradle.kts
├── index.html                # 모바일 인터랙티브 웹앱 (GitHub Pages 지원)
├── .nojekyll                 # GitHub Pages 정적 서빙 설정
└── README.md
```
