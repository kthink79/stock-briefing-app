---
title: "SAVE(세이브티커) 연동"
---

# SAVE(세이브티커) 연동 — saveticker.com

키워드 트레이스·기업 분석 뉴스에 SAVE PICK/로이터 기사를 `SAVE` 배지로 합치고, SAVE 계정에
등록된 관심 종목·키워드 알림과 경제 캘린더를 앱에 보여준다.

## 등록된 SAVE 관심 설정 (2026-11-23 기준, 슈탱구리·카카오 계정)
- 관심 종목(4/10): 삼성전자(005930) · SK하이닉스(000660) · 한화에어로스페이스(012450) · NVDA
- 키워드(4/5): 엔비디아 · HBM · 미국 이란 전쟁 · 트럼프
- 같은 값이 `data/config.json` 의 `save` 블록에 있다(앱의 전체 추적 목록과 구분해서 사용).

## GitHub Actions 가 직접 못 받는 이유 (중요)
- `https://www.saveticker.com/api/*` 는 Cloudflare 봇 차단(HTTP 403 "Just a moment") 뒤에 있다.
  쿠키 없는 curl·파이썬, 브라우저 TLS 위장(curl_cffi), 공개 프록시(r.jina.ai)로는 모두 막혔고,
  로그인된 브라우저 세션에서만 정상 JSON 이 온다. `api.saveticker.com` 은 레거시 API 로
  426 만 돌려준다.
- 그래서 `scripts/collect_live_data.py` 는 SAVE 를 **직접 조회 1회 시도 → 실패하면 저장소에
  커밋된 마지막 스냅샷(`data/save_latest.json`) 사용** 순서로 처리한다. 조회는 6초 타임아웃·
  8초 상한이라 `live-data.yml` 을 붙잡지 않는다(실측 0.1초).
- 스냅샷 신선도는 별도 작업이 만든다: 사용자 로그인 브라우저로 API 응답을 받아
  `scripts/save_snapshot_from_pdfs.py` 로 재생성 → 커밋. (스케줄 정의:
  `scheduled-actions/📰-save-뉴스-스냅샷-갱신/action.md`)

## 파일
- `data/save_latest.json` — SAVE 스냅샷(기사·주요 기사·경제 일정·관심 설정, `fetched_at`/`fetched_via`)
- `data/news_latest.json` 의 `save` 블록 — 앱이 읽는 요약(상태·건수·주요 기사·일정·관심 설정)
- `news.keywords[*].articles`, `news.companies[*].articles` — `origin: "SAVE"`, `badge: "SAVE PICK"|"SAVE"`
  기사가 함께 병합된다(같은 제목 중복 제거, 버킷당 6건 상한, 같은 제목의 SAVE 기사는 최신값으로 갱신).

## 스냅샷 재생성 절차
```
# 1) 로그인된 브라우저에서 아래 API 를 열고 각각 browser_pdf 로 저장
#    /api/news/top-stories
#    /api/news/list?page=1&page_size=20&sort=created_at_desc&keywords=엔비디아,HBM,미국 이란 전쟁,트럼프
#    /api/news/list?page=1&page_size=20&sort=created_at_desc&tickers=NVDA,000660.KR,005930.KR,012450.KR
#    /api/calendar/events?start_date=<오늘>&end_date=<+14일>
python3 scripts/save_snapshot_from_pdfs.py \
  --top ../browser/save_top.pdf --keywords ../browser/save_kw.pdf \
  --tickers ../browser/save_tkr.pdf --calendar ../browser/save_cal.pdf \
  --check-url "https://www.saveticker.com/news"
```
PDF 는 브라우저 인쇄본이라 페이지마다 꼬리표("pretty print 적용")가 붙고 공백이 제어문자(0x01)로
바뀌며 줄바꿈이 섞인다. 스크립트의 `repair_text()` 가 이를 복원해 JSON 을 뽑는다(테스트:
`crawler/tests/test_save_integration.py`).

## 앱 표시
- 키워드 트레이스 탭 상단 `🔗 SAVE 속보` 카드: 수집 경과("최신 · N분 전"), 주요 기사 4건,
  다음 경제 일정 3건, SAVE 관심 설정.
- 트레이스 타임라인·기업 분석 뉴스 목록: SAVE 기사에 `SAVE`/`SAVE PICK` 배지와
  `https://www.saveticker.com/news/<id>` 링크.
