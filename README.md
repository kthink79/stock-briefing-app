# 📈 stock-briefing-app

매일 오전 8시에 국내외 증시 및 반도체·우주항공 기술주 중심의 투자 정보를 수집하고 분석하여 리포트해 주는 서비스 & 안드로이드 애플리케이션입니다.  
**Gemini**의 심층 분석과 **Google Jules**를 통한 지속적인 코드 검증 및 개선 파이프라인을 지원합니다.

---

## 🚀 주요 기능

1. **글로벌 증시 & 거시경제 지표 수집**
   - 미국 3대 지수(다우, 나스닥, S&P 500) 및 필라델피아 반도체 지수(SOX) 마감 지표
   - 원/달러 환율, 미 국채 10년물 금리 등 거시경제 핵심 지표
2. **핵심 섹터 심층 모니터링**
   - **반도체**: 삼성전자, SK하이닉스, 엔비디아, TSMC, ASML 등 글로벌 공급망 동향
   - **우주항공**: 한화에어로스페이스, KAI, SpaceX, 미 방산 및 우주 탐사 주요 이슈
3. **Gemini 투자 분석 엔진**
   - 단순 나열이 아닌 투자자 관점의 장 개장 전 핵심 체크포인트 3가지 도출
4. **안드로이드 8 AM 모닝 브리핑 앱**
   - 매일 아침 08:00 정각 로컬 알림 발송
   - 일자별 리포트 아카이브 및 섹터별 필터링 UI
5. **Google Jules 기반 지속적 검증 & 최적화**
   - GitHub 연동을 통해 테스트 코드 자동 작성, 크롤러 예외 처리 강화, 성능 개선 PR 생성

---

## 📂 프로젝트 구조

```
stock-briefing-app/
├── crawler/                  # 데이터 수집 및 분석 모듈 (Python)
│   ├── crawler.py           # 증시 지표 및 기술주 뉴스 크롤러
│   ├── gemini_analyzer.py   # Gemini 프롬프트 및 분석 엔진
│   ├── requirements.txt     # 의존성 패키지
│   └── tests/
│       └── test_crawler.py  # 단위 테스트
├── android/                  # 안드로이드 애플리케이션 (Kotlin/Compose)
│   ├── app/
│   │   ├── src/main/java/com/kthink79/stockbriefing/
│   │   │   ├── MainActivity.kt
│   │   │   ├── model/Report.kt
│   │   │   └── notification/AlarmScheduler.kt
│   │   └── AndroidManifest.xml
│   └── build.gradle.kts
└── .github/
    └── workflows/
        └── ci.yml            # CI 자동 빌드 및 테스트 워크플로우
```

---

## 🤖 Jules & Gemini 활용 가이드

- **Jules 연동**: `jules.google.com`에서 `kthink79/stock-briefing-app`을 선택하고 새로운 기능 개발이나 버그 수정을 요청하면 Jules가 자동으로 코드를 수정하고 PR을 올립니다.
- **Gemini**: `crawler/gemini_analyzer.py`를 통해 수집된 원본 텍스트를 고품질 브리핑으로 가공합니다.
