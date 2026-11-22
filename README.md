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

---

## 🛰 실시간 데이터 아키텍처 (v3.0, 2026-11-17 전면 개편)

이전 버전은 `index.html` 안에 11월 초 시세와 기사를 하드코딩해 두고, `지금 전체 동기화` 버튼을 누르면
**로컬에서 임의의 등락폭만 흔드는 시뮬레이션**을 돌렸습니다. 그래서 시간이 지나도 기사·주가·키워드 트레이스가
그대로였습니다. v3.0은 실제 조회 경로로 바꿨습니다.

```
[GitHub Actions — 10분마다]
  scripts/collect_live_data.py
      ├─ 네이버 금융 실시간 polling API  → 국내/해외 주가·지수·금리·환율
      └─ Google News RSS               → 키워드별 최신 기사(중복 제거·KST 시각)
      ↓ 커밋
  data/market_latest.json , data/news_latest.json
      ↓ GitHub Pages (정적 서빙)
  index.html + app.js  ← 접속/동기화 시 ?t=캐시버스팅으로 즉시 fetch
```

| 파일 | 내용 |
| --- | --- |
| `data/market_latest.json` | 지수(코스피·코스닥·다우·나스닥·S&P500·SOX), 금리(미 10년·2년), 원/달러, 추적 종목 15개의 현재가·등락률·장전 시간외·시가총액, 자동 생성 시황 요약 |
| `data/news_latest.json` | 키워드별 기사(최근 10일), 섹터 헤드라인, 기업별 실적 기사, 사용자 키워드 대비 주제 풀 |
| `data/config.json` | 수집할 종목·키워드·섹터·주제 풀 (코드 수정 없이 편집 가능) |
| `data/stock_universe.json` | **전종목 검색 인덱스** — 코스피·코스닥 3,900여 종목 + 미국 580여 종목의 이름·코드·시장·현재가·등락률·시가총액·PER/PBR/EPS/BPS (10분 갱신) |
| `data/stock_fundamentals.json` | 국내 시가총액 상위 900종목(Daum 금융) + 미국 상위 40종목(네이버 TTM EPS·PBR) 재무지표 (하루 1회 갱신) |
| `data/us_symbols.json` | 미국 검색 대상 종목 코드 목록(S&P 500 + 나스닥100·ETF 보강, 600개) |
| `data/company_news.json` | 임의 종목 카드용 기업 뉴스 — 시가총액 상위 국내 120·미국 30 종목, 6시간 간격 회전 수집 |
| `scripts/collect_live_data.py` | 수집기 (표준 라이브러리만 사용, `--check`로 결과 미리보기) |
| `app.js` | 프런트엔드 실시간 엔진 (조회·적용·요약·중복 묶음) |

## 🔍 종목 검색·선택 등록 (v3.1, 2026-11-17)

기업 분석 탭의 입력창에 기업명·종목코드를 입력하면 **자동완성 후보 목록**이 뜨고, 후보를 누르면 바로 관심 목록에 등록됩니다.

- 범위: 코스피·코스닥 전 종목(3,900여) + 미국 주요 종목(580여) — 사전 등록된 12개에 한정되지 않습니다.
- 정렬: 코드/이름 정확 일치 → 코드 접두 → 이름 접두 → 이름 포함 → 시가총액 순.
- 조작: `↑↓` 이동, `Enter` 선택(정확 일치 1건이면 바로 등록), `Esc` 닫기, 마우스 클릭 등록.
- 이미 등록된 종목이면 중복 안내 후 해당 카드로 이동합니다.
- 목록에 없는 입력은 **빈 카드로 등록하지 않습니다** (v3.0까지는 `종목(코드)` 형태의 빈 카드가 생겼습니다).

### 카드에 표시되는 값
| 항목 | 출처 | 비고 |
| --- | --- | --- |
| 현재가·등락률·시가총액 | 네이버 금융 전종목 시세 (10분) | 전 종목 |
| PER·PBR·EPS·BPS | Daum 금융 (국내 상위 900) · 네이버 TTM EPS/PBR (미국 상위 40) | 수집 대상이 아니면 `-`로 표기 |
| 뉴스 | data/company_news.json (국내 120·미국 30) → 없으면 Google News RSS 실시간 조회 | CORS 때문에 브라우저 직접 호출은 프록시(rss2json) 경유 |
| 분기 실적(매출·영업이익) | 사전 등록된 12개 기업만 | 그 외 종목은 `실적 공시 데이터 미수집`으로 명시 |

브라우저는 외부 금융 API를 직접 호출할 수 없습니다(네이버·다음·야후 모두 CORS 차단).
그래서 **GitHub Actions가 수집해 같은 저장소의 JSON으로 배포**하고, 앱은 same-origin fetch만 사용합니다.

### 정직성 원칙- 조회에 성공하면 `🟢 실시간 · 기준 2026-11-17 08:32`처럼 **실제 데이터 기준 시각**을 표시합니다.
- 15분 이상 지난 파일만 있으면 `🟡 저장된 스냅샷`으로, 완전히 실패하면 `🔴 조회 실패`로 표시합니다.
- 어떤 경우에도 **임의의 가짜 시세·기사로 채우지 않습니다.**

### 자동 갱신 워크플로 등록
GitHub App 토큰에는 `workflows` 권한이 없어 이 저장소로는 워크플로 파일을 직접 푸시할 수 없습니다.
완성된 정의는 [`docs/workflows/live-data.yml`](docs/workflows/live-data.yml)에 있습니다.

1. GitHub 웹에서 이 저장소 → `Add file` → `Create new file`
2. 파일 경로: `.github/workflows/live-data.yml`
3. 위 파일 내용을 붙여넣고 `Commit changes`
4. 등록 후 `Actions` 탭에서 `live-data` → `Run workflow`로 한 번 수동 실행해 확인

워크플로가 없어도 앱은 마지막으로 커밋된 데이터를 계속 보여 줍니다(스냅샷 표시).

### 키워드·종목 추가`data/config.json`의 `trace_keywords`(기본 트레이스) · `keyword_pool`(사용자가 추가한 키워드용 예비 수집 목록) ·
`kr_stocks` / `us_stocks`(기업 분석 목록)를 수정하면 다음 수집 주기부터 반영됩니다.

### 워크플로 파일
`.github/workflows/live-data.yml`은 표준 YAML 형식으로 저장소에 있으며(편집은 GitHub 웹의 **Upload files** 권장 —
웹 편집기의 자동 들여쓰기가 내용을 망가뜨릴 수 있습니다), 같은 내용의 사본이 `docs/workflows/live-data.yml`에 있습니다.
GitHub App 토큰에는 `workflows` 권한이 없어 CLI(`git push`)로는 이 파일을 올릴 수 없고, 사용자의 GitHub 웹 세션으로 올렸습니다.

### 운영 cadence
- 시세: 10분마다 (`2,12,22,32,42,52` — GitHub 스케줄러가 혼잡한 정각을 피하도록 분 단위를 어긋나게 지정)
- 기본 키워드 뉴스: 20분마다 / 섹터·기업 뉴스: 60분마다(회전 수집) / 주제 풀: 120분마다
- 값이 실제로 바뀐 실행에서만 커밋하고, 변화가 없는 야간에는 30분에 한 번만 타임스탬프를 갱신합니다. (`data/.last_run.json`은 커밋하지 않습니다)

### 수집 시간 상한(멈춤 방지)
2026-11-22 03:17 KST 실행(run `44934697099`)이 10분간 아무 출력 없이 멈춰 취소된 뒤로,
`scripts/collect_live_data.py`는 모든 외부 호출에 세 겹의 시간 상한을 둡니다.

| 단계 | 기본값 | 환경변수 |
| --- | --- | --- |
| 소켓 connect/read 타임아웃 | 10초 | `COLLECT_HTTP_TIMEOUT` |
| 호출 1회 벽시계 상한(DNS 조회·느린 응답 포함) | 20초 | `COLLECT_MAX_CALL_SECONDS` |
| 필수 수집 실행 예산 | 300초 | `COLLECT_BUDGET_SECONDS` |
| 뉴스 / 전종목 / 재무지표 구간 예산 | 150 / 120 / 90초 | `COLLECT_NEWS_BUDGET_SECONDS` 등 |
| 워치독 강제 종료 | 420초 | `COLLECT_HARD_LIMIT_SECONDS` |

- 진행 로그를 stderr 로 즉시 남기므로(`[market] 5.8초`, `[news] 수집: HBM` …) 멈추면 어느 단계인지 바로 보입니다.
- 종목·지표·키워드 하나가 실패하거나 예산을 넘기면 그 항목만 건너뛰고 **직전 값 유지(`stale`)** 로 실행을 끝까지 진행합니다.
- 워크플로는 수집 스텝 `timeout-minutes: 8` / job `timeout-minutes: 12` 로 걸려 있어 스크립트가 멈춰도 10분을 넘기지 않습니다.
- 회귀 테스트: `python3 -m unittest discover -s crawler/tests` — `test_live_data_resilience.py` 가 무응답·drip 서버 차단, 부분 실패 유지, 예산 준수를 검증합니다.

### 자동 실행 확인
```bash
gh run list -R kthink79/stock-briefing-app -w live-data --event schedule -L 3
```
스케줄 실행이 보이지 않으면 Actions 탭에서 `live-data` → **Run workflow**로 즉시 한 번 돌릴 수 있습니다.

