#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""실시간 시세·뉴스 수집기 (stock-briefing-app 백엔드)

GitHub Actions에서 주기적으로 실행되어 아래 두 파일을 저장소에 기록한다.
  data/market_latest.json  : 국내/해외 주가·지수·금리·환율 (네이버 금융 실시간)
  data/news_latest.json    : 키워드별 최신 뉴스 (Google News RSS)

정적 호스팅(GitHub Pages) 앱은 이 JSON을 `?t=<timestamp>` 캐시버스팅으로
fetch 하여 "실제 최신" 데이터를 표시한다. 네트워크/파싱 실패 시에는
직전 파일의 값을 유지하고 errors/stale 표시를 남긴다(가짜 값 생성 금지).

사용:
  python3 scripts/collect_live_data.py [--check]
    --check : 파일을 쓰지 않고 수집 결과 요약만 출력(테스트/로컬 확인용)
"""
from __future__ import annotations

import argparse
import json
import os
import re
import signal
import socket
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from functools import lru_cache

KST = timezone(timedelta(hours=9))
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(ROOT, "data")
MARKET_PATH = os.path.join(DATA_DIR, "market_latest.json")
NEWS_PATH = os.path.join(DATA_DIR, "news_latest.json")
CONFIG_PATH = os.path.join(DATA_DIR, "config.json")

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")

NAVER_POLL = "https://polling.finance.naver.com/api/realtime"
NAVER_PRODUCT = "https://m.stock.naver.com/front-api/marketIndex/productDetail"
NAVER_LIST = "https://m.stock.naver.com/api/stocks/marketValue"
DAUM_QUOTE = "https://finance.daum.net/api/quotes/A"
GOOGLE_NEWS = "https://news.google.com/rss/search"

# 기준금리(정책금리) 공식 소스 — 하드코딩 금지, 조회 실패 시 직전 성공값 유지
NYFED_EFFR = "https://markets.newyorkfed.org/api/rates/unsecured/effr/last/1.json"
FRED_TARGET_CSV = "https://fred.stlouisfed.org/graph/fredgraph.csv?id=DFEDTARU,DFEDTARL"
BOK_BASE_RATE = ("https://www.bok.or.kr/portal/singl/baseRate/list.do"
                 "?dataSeCd=01&menuNo=200643")
POLICY_RATE_REUSE_MINUTES = 60   # 기준금리는 하루 몇 번만 바뀐다 — 1시간 캐시 후 재조회

# --------------------------------------------------------------------------
# 네트워크 타임아웃 · 실행 예산
#   2026-11-22 03:17 KST 실행(run 44934697099)에서 스크립트가 10분간 아무 출력 없이
#   멈춰 job timeout 으로 취소됐다. urllib 의 timeout 은 소켓 읽기에는 걸리지만
#   DNS 조회나 아주 느린 응답(drip)에는 걸리지 않아 한 번의 호출이 실행 전체를
#   붙잡을 수 있다. 그래서 모든 외부 호출에 (1) 소켓 타임아웃, (2) 호출 1회
#   벽시계(wall-clock) 상한, (3) 실행 전체 예산을 함께 둔다. 환경변수로 조정 가능.
# --------------------------------------------------------------------------
def _env_number(name: str, default: float) -> float:
    try:
        value = float(os.environ.get(name, default))
    except (TypeError, ValueError):
        return float(default)
    return value if value > 0 else float(default)


HTTP_TIMEOUT = _env_number("COLLECT_HTTP_TIMEOUT", 10)          # connect/read (socket)
HTTP_RETRIES = int(_env_number("COLLECT_HTTP_RETRIES", 1))     # 추가 재시도 횟수
MAX_CALL_SECONDS = _env_number("COLLECT_MAX_CALL_SECONDS", 20)  # 호출 1회 벽시계 상한
RUN_BUDGET_SECONDS = _env_number("COLLECT_BUDGET_SECONDS", 300)  # 필수 수집 예산
HARD_LIMIT_SECONDS = _env_number("COLLECT_HARD_LIMIT_SECONDS", 420)  # 워치독 강제 종료
NEWS_BUDGET_SECONDS = _env_number("COLLECT_NEWS_BUDGET_SECONDS", 150)
UNIVERSE_BUDGET_SECONDS = _env_number("COLLECT_UNIVERSE_BUDGET_SECONDS", 120)
FUND_BUDGET_SECONDS = _env_number("COLLECT_FUND_BUDGET_SECONDS", 90)
MAX_RESPONSE_BYTES = 8 * 1024 * 1024
STARTED_AT = time.monotonic()

# timeout 인자를 빠뜨린 라이브러리도 최소한 소켓 단계에서 무한 대기하지 않도록 한다.
if socket.getdefaulttimeout() is None:
    socket.setdefaulttimeout(HTTP_TIMEOUT)

# 전종목 검색·등록용 데이터 파일
UNIVERSE_PATH = os.path.join(DATA_DIR, "stock_universe.json")
FUND_PATH = os.path.join(DATA_DIR, "stock_fundamentals.json")
US_SYMBOLS_PATH = os.path.join(DATA_DIR, "us_symbols.json")
COMPANY_NEWS_PATH = os.path.join(DATA_DIR, "company_news.json")
UNIVERSE_FUND_LIMIT = 900      # 재무지표(PER/PBR)를 수집할 국내 종목 수 (시가총액 상위)
UNIVERSE_FUND_MIN_HOUR = 7     # KST 07시 이후에만 하루 1회 재무지표 갱신
COMPANY_NEWS_KR_LIMIT = 120    # 뉴스를 미리 모아둘 국내 종목 수 (시가총액 상위)
COMPANY_NEWS_US_LIMIT = 30     # 미국 종목 수
COMPANY_NEWS_PER_RUN = 8       # 회전 수집: 한 실행에 새로 수집할 종목 수
COMPANY_NEWS_REUSE_HOURS = 6   # 같은 종목 재수집 간격
US_FUND_LIMIT = 40             # 미국 종목 재무지표(PER/PBR) 수집 대상 (시가총액 상위)
DEFAULT_CONFIG = {
    "kr_stocks": {
        "005930": "삼성전자",
        "000660": "SK하이닉스",
        "012450": "한화에어로스페이스",
        "005380": "현대차",
        "035420": "NAVER",
        "042700": "한미반도체",
        "034020": "두산에너빌리티",
        "035720": "카카오",
        "068270": "셀트리온",
    },
    "us_stocks": {
        "NVDA": "NVDA.O",
        "TSLA": "TSLA.O",
        "AAPL": "AAPL.O",
        "AMD": "AMD.O",
        "AVGO": "AVGO.O",
        "MU": "MU.O",
    },
    "kr_indices": ["KOSPI", "KOSDAQ"],
    "us_indices": {
        "DOW": ".DJI",
        "NASDAQ": ".IXIC",
        "SP500": ".INX",
        "SOX": ".SOX",
    },
    "rates": {"US10Y": "US10YT=RR", "US2Y": "US2YT=RR"},
    "fx": {"USDKRW": "FX_USDKRW"},
    "trace_keywords": [
        {"id": "trace_nvda", "keyword": "엔비디아", "query": "엔비디아", "require": ["엔비디아"]},
        {"id": "trace_hbm", "keyword": "HBM", "query": "HBM", "require": ["HBM"]},
        {"id": "trace_iran", "keyword": "미국 이란 전쟁", "query": "이란", "require": ["이란"],
         "context_any": ["전쟁", "분쟁", "군사", "무력", "공격", "타격", "충돌", "공습",
                         "미사일", "드론", "기뢰", "호르무즈", "해협", "유조선", "원유",
                         "유가", "석유", "이스라엘", "네타냐후", "미국", "트럼프", "제재",
                         "핵", "중동", "외교", "협상", "중재", "혁명수비대", "파병",
                         "휴전", "청해부대", "걸프"]},
        {"id": "trace_trump", "keyword": "트럼프", "query": "트럼프", "require": ["트럼프"]},
    ],
    "sector_news": [
        {"tag": "반도체", "query": "반도체 수출"},
        {"tag": "국내증시", "query": "코스피 외국인"},
        {"tag": "AI", "query": "AI 데이터센터 투자"},
    ],
    "keyword_pool": [
        "삼성전자", "SK하이닉스", "반도체", "AI", "HBM", "금리", "환율",
        "비트코인", "현대차", "방산", "조선", "원전", "중국", "관세",
        "연준", "물가", "코스피", "테슬라", "애플", "2차전지", "바이오",
        "우주항공", "메모리", "엔비디아", "트럼프", "이란",
    ],
    "news_filter": {
        "noise_exclude": [
            "신간", "출간", "도서", "저자", "서평", "베스트셀러", "사형", "고문",
            "반정부", "시위", "인권", "부고", "별세", "결혼", "이혼", "연예",
            "드라마", "예능", "아이돌", "야구", "축구", "농구", "올림픽",
        ],
        "market_context": [
            "증시", "주가", "주식", "코스피", "코스닥", "시가총액", "시총", "실적",
            "영업이익", "매출", "상장", "공시", "수주", "수출", "투자", "인수",
            "합병", "M&A", "금리", "환율", "반도체", "공급", "계약", "특징주",
            "종목", "펀드", "ETF", "배당", "유가", "원유", "달러", "지수", "선물",
            "채권", "증권", "매수", "매도",
        ],
    },
    "news_max_age_days": 10,
    "news_per_keyword": 30,
    "news_reuse_minutes": 20,
    "sector_reuse_minutes": 60,
    "company_reuse_minutes": 60,
    "company_per_run": 2,
    "pool_reuse_minutes": 120,
    "pool_per_run": 3,
}


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------
def now_kst() -> datetime:
    return datetime.now(KST)


def log(message: str) -> None:
    """진행 로그는 stderr 로 즉시 flush — 멈춘 지점을 로그만 보고 알 수 있게 한다."""
    print(message, file=sys.stderr, flush=True)


def elapsed_seconds() -> float:
    return time.monotonic() - STARTED_AT


def budget_left(limit: float | None = None) -> float:
    """실행 시작 후 남은 시간(초). limit=None 이면 필수 수집 예산 기준."""
    limit = RUN_BUDGET_SECONDS if limit is None else limit
    return limit - elapsed_seconds()


class FetchTimeout(TimeoutError):
    """한 번의 외부 호출이 정해진 시간 안에 끝나지 않았을 때."""


def _can_use_alarm() -> bool:
    return (hasattr(signal, "setitimer") and hasattr(signal, "SIGALRM")
            and threading.current_thread() is threading.main_thread())


def run_with_timeout(fn, seconds: float, label: str = "fetch"):
    """fn() 을 seconds 초 안에 끝내고 결과를 돌려준다. 넘기면 FetchTimeout.

    메인 스레드에서는 SIGALRM 으로 DNS 조회·느린 응답까지 끊고, 신호를 쓸 수 없는
    워커 스레드에서는 데몬 스레드로 실행해 기다리다가 포기한다(멈춘 스레드는
    데몬이므로 프로세스 종료를 막지 않는다).
    """
    seconds = float(seconds)
    if seconds <= 0:
        raise FetchTimeout(f"{label}: 시간 예산 소진")
    if _can_use_alarm():
        def _on_alarm(signum, frame):  # noqa: ANN001
            raise FetchTimeout(f"{label}: {seconds:.1f}초 초과")

        previous = signal.signal(signal.SIGALRM, _on_alarm)
        signal.setitimer(signal.ITIMER_REAL, seconds)
        try:
            return fn()
        finally:
            signal.setitimer(signal.ITIMER_REAL, 0)
            signal.signal(signal.SIGALRM, previous)

    box: dict = {}

    def _target():
        try:
            box["value"] = fn()
        except BaseException as e:  # noqa: BLE001 - 워커 오류를 호출자에게 그대로 전달
            box["error"] = e

    worker = threading.Thread(target=_target, daemon=True, name=f"fetch-{label[:24]}")
    worker.start()
    worker.join(seconds)
    if worker.is_alive():
        raise FetchTimeout(f"{label}: {seconds:.1f}초 초과")
    if "error" in box:
        raise box["error"]
    return box.get("value")


def install_watchdog(limit: float | None = None) -> threading.Thread:
    """최후의 안전장치: limit 초를 넘기면 로그를 남기고 프로세스를 끝낸다.

    정상 경로에서는 각 구간이 예산을 지켜 먼저 끝나므로 이 워치독은 발동하지 않는다.
    """
    limit = HARD_LIMIT_SECONDS if limit is None else float(limit)

    def _watch():
        time.sleep(limit)
        log(f"[fatal] 수집이 {limit:.0f}초를 넘겨 강제 종료합니다 — 이번 실행 데이터는 갱신되지 않았습니다")
        try:
            sys.stdout.flush()
            sys.stderr.flush()
        except Exception:  # noqa: BLE001
            pass
        os._exit(2)

    thread = threading.Thread(target=_watch, name="collect-watchdog", daemon=True)
    thread.start()
    return thread


def num(value):
    """'1,234.5' / '-0.24%' / '-' 등을 float로 변환. 값이 없으면 None."""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).replace(",", "").replace("%", "").replace("+", "").strip()
    if text in ("", "-", "N/A", "null", "None"):
        return None
    try:
        return float(text)
    except ValueError:
        return None


def _urlopen_read(req: urllib.request.Request, timeout: float, limit: float,
                  label: str) -> bytes:
    """요청 1회를 벽시계 상한 안에서 수행한다.

    urlopen(timeout=) 은 connect/read 소켓에만 걸리므로, DNS 조회나
    바이트를 조금씩 보내는 서버까지 막으려면 바깥에서 한 번 더 시간을 잡는다.
    """
    def _do():
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.read(MAX_RESPONSE_BYTES + 1)

    body = run_with_timeout(_do, min(timeout, limit) + 1.0, label=label)
    if body is not None and len(body) > MAX_RESPONSE_BYTES:
        raise RuntimeError(f"응답이 너무 큽니다({len(body)} bytes): {label}")
    return body


def http_get(url: str, timeout: float | None = None, retries: int | None = None,
             max_seconds: float | None = None) -> bytes:
    """외부 HTTP GET — 소켓 타임아웃 + 호출별 벽시계 상한 + 재시도 상한.

    한 엔드포인트가 멈춰도 이 호출만 실패하고, 실행 전체는 예산 안에서 계속된다.
    """
    timeout = HTTP_TIMEOUT if timeout is None else float(timeout)
    retries = HTTP_RETRIES if retries is None else int(retries)
    per_call = MAX_CALL_SECONDS if max_seconds is None else float(max_seconds)
    label = url.split("?")[0]
    last_err = None
    for attempt in range(retries + 1):
        left = min(budget_left(), per_call)
        if left <= 0.2:
            last_err = last_err or f"시간 예산 소진 ({url})"
            break
        req = urllib.request.Request(url, headers={
            "User-Agent": UA,
            "Accept": "*/*",
            "Accept-Language": "ko-KR,ko;q=0.9,en;q=0.8",
        })
        try:
            return _urlopen_read(req, timeout, left, label)
        except urllib.error.HTTPError as e:  # 429/500 등
            last_err = f"HTTP {e.code} {url}"
            if e.code in (429, 500, 502, 503, 504) and attempt < retries:
                time.sleep(min(1.5 * (attempt + 1), max(0.0, budget_left() - 0.5)))
                continue
            raise RuntimeError(last_err)
        except Exception as e:  # 타임아웃·DNS·연결·응답 오류
            last_err = f"{type(e).__name__}: {e} ({url})"
            log(f"[warn] HTTP 실패 {attempt + 1}/{retries + 1}: {last_err}")
            if attempt < retries:
                time.sleep(min(1.0 * (attempt + 1), max(0.0, budget_left() - 0.5)))
    raise RuntimeError(last_err or f"request failed: {url}")


def get_json(url: str, **kwargs) -> dict:
    return json.loads(http_get(url, **kwargs).decode("utf-8", "replace"))


def iso_kst(dt: datetime) -> str:
    return dt.astimezone(KST).isoformat(timespec="seconds")


# --------------------------------------------------------------------------
# parsing: 네이버 금융 실시간
# --------------------------------------------------------------------------
def parse_naver_quote(item: dict, currency_default: str = "KRW") -> dict:
    """네이버 polling 응답의 한 종목/지수를 정규화한다."""
    price = num(item.get("closePrice"))
    diff = num(item.get("compareToPreviousClosePrice"))
    pct = num(item.get("fluctuationsRatio"))
    prev_close = None
    if price is not None and diff is not None:
        prev_close = round(price - diff, 4)

    currency = currency_default
    ctype = item.get("currencyType") or {}
    if ctype.get("code"):
        currency = ctype["code"]

    quote = {
        "name": item.get("stockName") or item.get("indexName") or item.get("itemCode"),
        "price": price,
        "prev_close": prev_close,
        "change": diff,
        "pct": pct,
        "currency": currency,
        "market_status": item.get("marketStatus"),
        "as_of": item.get("localTradedAt"),
        "high": num(item.get("highPrice")),
        "low": num(item.get("lowPrice")),
        "open": num(item.get("openPrice")),
        "volume": num(item.get("accumulatedTradingVolume")),
        "market_value": num(item.get("marketValueFull")),
    }

    # 장전/시간외 거래가 있으면 별도로 표기(프리마켓/애프터마켓)
    over = item.get("overMarketPriceInfo") or {}
    session_type = str(over.get("tradingSessionType") or "").upper()
    # 정규장 중에는 overMarketPriceInfo가 정규장 값이므로 '시간외'로 표기하지 않는다
    if over and num(over.get("overPrice")) is not None and session_type and session_type != "REGULAR_MARKET":
        quote["extended"] = {
            "session": over.get("tradingSessionType"),
            "price": num(over.get("overPrice")),
            "pct": num(over.get("fluctuationsRatio")),
            "change": num(over.get("compareToPreviousClosePrice")),
            "as_of": over.get("localTradedAt"),
            "volume": num(over.get("accumulatedTradingVolumeRaw")),
        }
    return quote


def fetch_domestic_stocks(codes) -> dict:
    if not codes:
        return {}
    url = f"{NAVER_POLL}/domestic/stock/{','.join(codes)}"
    payload = get_json(url)
    out = {}
    for item in payload.get("datas", []):
        code = item.get("itemCode") or item.get("symbolCode")
        if code:
            out[code] = parse_naver_quote(item, "KRW")
    return out


def fetch_domestic_index(codes) -> dict:
    if not codes:
        return {}
    url = f"{NAVER_POLL}/domestic/index/{','.join(codes)}"
    payload = get_json(url)
    out = {}
    for item in payload.get("datas", []):
        code = (item.get("itemCode") or "").upper()
        if code:
            out[code] = parse_naver_quote(item, "KRW")
    return out


def fetch_world_stocks(mapping: dict) -> dict:
    """mapping: {앱코드: 네이버 reuters code}"""
    if not mapping:
        return {}
    rev = {v: k for k, v in mapping.items()}
    url = f"{NAVER_POLL}/worldstock/stock/{','.join(mapping.values())}"
    payload = get_json(url)
    out = {}
    for item in payload.get("datas", []):
        rcode = item.get("reutersCode") or item.get("symbolCode")
        key = rev.get(rcode) or rcode
        quote = parse_naver_quote(item, "USD")
        if not quote["name"]:
            quote["name"] = key
        out[key] = quote
    return out


def fetch_world_index(mapping: dict) -> dict:
    """mapping: {앱키: 네이버 지수 코드}"""
    if not mapping:
        return {}
    rev = {v: k for k, v in mapping.items()}
    url = f"{NAVER_POLL}/worldstock/index/{','.join(mapping.values())}"
    payload = get_json(url)
    out = {}
    for item in payload.get("datas", []):
        rcode = item.get("reutersCode") or item.get("itemCode")
        key = rev.get(rcode) or rcode
        out[key] = parse_naver_quote(item, "USD")
    return out


def fetch_product(category: str, reuters_code: str) -> dict | None:
    query = urllib.parse.urlencode({"category": category, "reutersCode": reuters_code})
    try:
        payload = get_json(f"{NAVER_PRODUCT}?{query}")
    except Exception as e:  # noqa: BLE001 - 한 상품 실패는 다른 상품을 막지 않는다
        log(f"[warn] {category} {reuters_code} 조회 실패: {e}")
        return None
    result = payload.get("result") or {}
    price = num(result.get("closePrice"))
    if price is None:
        return None
    return {
        "name": result.get("name"),
        "price": price,
        "change": num(result.get("fluctuations")),
        "pct": num(result.get("fluctuationsRatio")),
        "currency": (result.get("currencyType") or {}).get("code"),
        "market_status": result.get("marketStatus"),
        "as_of": result.get("localTradedAt"),
    }


def fetch_rates(mapping: dict) -> dict:
    out = {}
    for key, code in mapping.items():
        q = fetch_product("bond", code)
        if q:
            # 금리는 bp 변화도 함께 (10년물 5.129% → -0.0239 = -2.39bp)
            if q.get("change") is not None:
                q["bp"] = round(q["change"] * 100, 2)
            out[key] = q
    return out


def fetch_fx(mapping: dict) -> dict:
    out = {}
    for key, code in mapping.items():
        q = fetch_product("exchange", code)
        if q:
            out[key] = q
    return out


# --------------------------------------------------------------------------
# 기준금리(정책금리): 연준 목표범위 · 한국은행 기준금리
#   - 값과 함께 '기준일'(연준 적용일 / 한은 변경일)과 출처를 남긴다.
#   - 조회가 실패하면 절대 임의의 기본값으로 되돌리지 않고,
#     직전 성공값을 stale=True 로 유지하거나 status='unavailable' 로 비운다.
# --------------------------------------------------------------------------
def parse_nyfed_policy(payload: dict) -> dict | None:
    """뉴욕연은 markets API(refRates) → 연준 목표범위."""
    rows = (payload or {}).get("refRates") or []
    if not rows:
        return None
    row = rows[0] or {}
    low = num(row.get("targetRateFrom"))
    high = num(row.get("targetRateTo"))
    if low is None and high is None:
        return None
    if low is not None and high is not None and low > high:
        low, high = high, low
    return {
        "low": low,
        "high": high,
        "effr": num(row.get("percentRate")),
        "as_of": row.get("effectiveDate"),
        "source": "미 연방준비제도 목표범위 (뉴욕연은 공표 EFFR)",
        "url": NYFED_EFFR,
    }


def parse_fred_target(csv_text: str) -> dict | None:
    """FRED fredgraph.csv(DFEDTARU/DFEDTARL) → 연준 목표범위(보조 소스)."""
    lines = [ln.strip() for ln in (csv_text or "").splitlines() if ln.strip()]
    if len(lines) < 2:
        return None
    header = [h.strip().strip('"').lower() for h in lines[0].split(",")]
    try:
        i_up = header.index("dfedtaru")
        i_low = header.index("dfedtarl")
    except ValueError:
        return None
    for line in reversed(lines[1:]):
        cells = [c.strip().strip('"') for c in line.split(",")]
        if len(cells) <= max(i_up, i_low):
            continue
        low, high = num(cells[i_low]), num(cells[i_up])
        if low is None and high is None:
            continue
        return {
            "low": low,
            "high": high,
            "effr": None,
            "as_of": cells[0],
            "source": "미 연방준비제도 목표범위 (FRED DFEDTARU/DFEDTARL)",
            "url": FRED_TARGET_CSV,
        }
    return None


_BOK_ROW_RE = re.compile(
    r"<td[^>]*>\s*(\d{4})\s*</td>\s*<td[^>]*>\s*(\d{1,2})\s*월\s*(\d{1,2})\s*일\s*</td>"
    r"\s*<td[^>]*>\s*(\d+(?:\.\d+)?)\s*</td>")


def parse_bok_base_rate(html: str) -> dict | None:
    """한국은행 '기준금리 추이' 표 → 가장 최근 변경 기준금리.

    표의 첫 행이 최신 변경이며, 그 행의 연/월/일이 곧 현재 기준금리의 기준일이다.
    """
    m = _BOK_ROW_RE.search(html or "")
    if not m:
        return None
    year, month, day, rate = m.groups()
    value = num(rate)
    if value is None or not (0 < value < 30):
        return None
    return {
        "rate": value,
        "as_of": f"{int(year):04d}-{int(month):02d}-{int(day):02d}",
        "source": "한국은행 기준금리 추이 (bok.or.kr)",
        "url": BOK_BASE_RATE,
    }


def fetch_us_policy_rate() -> dict:
    """연준 기준금리 목표범위: 뉴욕연은 API → FRED 순으로 시도."""
    errors = []
    try:
        parsed = parse_nyfed_policy(get_json(NYFED_EFFR))
        if parsed:
            return parsed
        errors.append("뉴욕연은 응답에 목표범위 없음")
    except Exception as e:
        errors.append(f"뉴욕연은 조회 실패: {e}")
    try:
        csv_text = http_get(FRED_TARGET_CSV, timeout=8, retries=1,
                            max_seconds=12).decode("utf-8", "replace")
        parsed = parse_fred_target(csv_text)
        if parsed:
            return parsed
        errors.append("FRED 응답에 목표범위 없음")
    except Exception as e:
        errors.append(f"FRED 조회 실패: {e}")
    raise RuntimeError("; ".join(errors))


def fetch_kr_policy_rate() -> dict:
    """한국은행 기준금리: 기준금리 추이 표 파싱."""
    html = http_get(BOK_BASE_RATE, timeout=12, retries=1,
                    max_seconds=18).decode("utf-8", "replace")
    parsed = parse_bok_base_rate(html)
    if not parsed:
        raise RuntimeError("한국은행 기준금리 표를 찾지 못했습니다")
    return parsed


def _carry_policy_rate(old: dict, checked_at: str) -> dict:
    """직전 성공값을 그대로 유지하되 stale 표시(가짜 기본값으로 대체하지 않는다)."""
    kept = dict(old)
    kept["status"] = "stale"
    kept["stale"] = True
    kept["checked_at"] = checked_at
    return kept


def collect_policy_rates(previous: dict) -> tuple[dict, list]:
    """미/한 기준금리를 공식 소스에서 조회한다.

    반환: ({'US': {...}, 'KR': {...}}, errors)
    각 항목은 status(ok|stale|unavailable)·as_of(기준일)·source·checked_at 를 갖는다.
    """
    prev = ((previous or {}).get("market") or {}).get("policy_rates") or {}
    checked_at = iso_kst(now_kst())
    out = {}
    errors = []
    for key, fn, label in (("US", fetch_us_policy_rate, "미국 기준금리"),
                           ("KR", fetch_kr_policy_rate, "한국 기준금리")):
        old = prev.get(key) or {}
        has_value = (old.get("low") is not None or old.get("high") is not None
                     or old.get("rate") is not None)
        # 값은 하루 몇 번만 바뀌므로 신선하면 재조회하지 않는다(소스 서버 부담·시간 절약)
        if has_value and old.get("status") == "ok" and is_fresh(old.get("checked_at"),
                                                               POLICY_RATE_REUSE_MINUTES):
            out[key] = old
            continue
        try:
            fresh = fn()
            fresh["status"] = "ok"
            fresh["stale"] = False
            fresh["checked_at"] = checked_at
            out[key] = fresh
        except Exception as e:
            errors.append(f"{label} 조회 실패: {e}")
            if has_value:
                out[key] = _carry_policy_rate(old, checked_at)
                errors.append(f"{label} 직전 성공값 유지({old.get('as_of') or '기준일 미상'})")
            else:
                out[key] = {"status": "unavailable", "stale": True,
                            "checked_at": checked_at, "as_of": None,
                            "error": str(e)}
    return out, errors


# --------------------------------------------------------------------------
# parsing: Google News RSS
# --------------------------------------------------------------------------
_TAG_RE = re.compile(r"\[[^\]]{1,12}\]|\([^)]{1,12}\)")
_WS_RE = re.compile(r"\s+")


def clean_title(title: str, source: str = "") -> str:
    title = (title or "").strip()
    if source:
        for sep in (" - ", " – ", " | "):
            if title.endswith(sep + source):
                title = title[: -len(sep + source)]
                break
    return _WS_RE.sub(" ", title).strip()


def title_key(title: str) -> str:
    """중복 판단용 정규화 키: 대괄호 태그/기호/공백 제거."""
    t = _TAG_RE.sub(" ", title or "")
    t = re.sub(r"[^0-9A-Za-z가-힣]", "", t).lower()
    return t


def parse_google_news_rss(xml_bytes: bytes, keyword: str, max_age_days: int = 10,
                          limit: int = 30) -> list:
    articles = []
    cutoff = now_kst() - timedelta(days=max_age_days)
    root = ET.fromstring(xml_bytes)
    channel = root.find("channel")
    if channel is None:
        return articles
    seen = set()
    for item in channel.findall("item"):
        raw_title = item.findtext("title") or ""
        source_el = item.find("source")
        source = (source_el.text or "").strip() if source_el is not None else ""
        link = item.findtext("link") or ""
        pub = item.findtext("pubDate") or ""
        try:
            dt = parsedate_to_datetime(pub)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            dt = dt.astimezone(KST)
        except Exception:
            continue
        if dt < cutoff:
            continue
        title = clean_title(raw_title, source)
        key = title_key(title)
        if not key or key in seen:
            continue
        seen.add(key)
        articles.append({
            "keyword": keyword,
            "title": title,
            "source": source or "Google News",
            "url": link,
            "date": dt.strftime("%Y-%m-%d"),
            "time": dt.strftime("%H:%M"),
            "published_at": iso_kst(dt),
        })
        if len(articles) >= limit:
            break
    articles.sort(key=lambda a: a["published_at"], reverse=True)
    return articles


# --------------------------------------------------------------------------
# 키워드 3중 필터 — 키워드 트레이싱 오탐 차단
#   ① 단어 경계 : '수학이란'·'창당이란'·'무엇이란'·'이란성'처럼 다른 단어의 일부로
#                  들어간 '이란'은 매칭하지 않는다(서술격 조사 '-(이)란' 대응).
#   ② 복합 맥락 : '미국 이란 전쟁'은 '이란' 단독으로 통과시키지 않고 전쟁·분쟁·군사·
#                  호르무즈·유가·원유·이스라엘·미국·트럼프·제재·미사일·드론·공습·
#                  기뢰·외교·핵·중동 같은 지정학·시장 맥락이 함께 있을 때만 통과시킨다.
#   ③ 비증시 노이즈: 신간·도서·출간·저자·사형·고문·반정부시위·인권·부고·결혼·연예·
#                  드라마·야구·축구 같은 사회/문화 기사는 제외한다. 단 주가·실적·수출
#                  처럼 명백한 경제·증시·기업 신호가 함께 있으면 예외로 남긴다.
#   같은 규칙을 Google News 수집·SAVE 병합·기존 저장분 재검사·앱(app.js)에 함께 적용한다.
# --------------------------------------------------------------------------
_HANGUL = "가-힣"
# 서술격 조사 '-(이)란' 과 모양이 같아 앞 글자에 붙으면 다른 말이 되는 키워드
_COPULA_TOKENS = frozenset({"이란", "라는", "란"})
# 같은 표기지만 다른 뜻인 고유명사 — 매칭 전에 지운다(경계 규칙으로는 못 가른다)
TOKEN_BLOCK_PHRASES = {
    "조선": ("조선일보", "조선비즈", "조선노동당", "조선미녀", "조선왕조", "조선시대",
            "조선 후기", "조선족", "조선대학교"),
    "이란": ("이란성",),
    "애플": ("애플망고", "그린 애플", "애플 어워즈"),
}
# 지정학·시장 맥락(복합 맥락 필수 키워드)
GEOPOLITICAL_CONTEXT = [
    "전쟁", "분쟁", "군사", "무력", "공격", "타격", "충돌", "공습", "미사일",
    "드론", "기뢰", "호르무즈", "해협", "유조선", "원유", "유가", "석유",
    "이스라엘", "네타냐후", "미국", "트럼프", "제재", "핵", "중동", "외교",
    "협상", "중재", "혁명수비대", "파병", "휴전", "청해부대", "걸프",
]
# 키워드별 기본 맥락(config 에 context_any 가 없을 때)
DEFAULT_CONTEXT = {
    "미국 이란 전쟁": GEOPOLITICAL_CONTEXT,
    "이란": GEOPOLITICAL_CONTEXT,
}
# 비증시·사회/문화 노이즈
NOISE_TOKENS = (
    "신간", "출간", "도서", "저자", "서평", "베스트셀러", "사형", "고문",
    "반정부", "시위", "인권", "부고", "별세", "결혼", "이혼", "연예",
    "드라마", "예능", "아이돌", "야구", "축구", "농구", "올림픽",
)
# 한 글자라 낱말 경계로만 판정해야 하는 노이즈('책임' 오탐 방지)
NOISE_WORD_TOKENS = ("책",)
# 노이즈 단어가 있어도 이 신호가 함께 있으면 경제·증시·기업 기사로 보고 남긴다
MARKET_CONTEXT_TOKENS = (
    "증시", "주가", "주식", "코스피", "코스닥", "시가총액", "시총", "실적",
    "영업이익", "매출", "상장", "공시", "수주", "수출", "투자", "인수", "합병",
    "M&A", "금리", "환율", "반도체", "공급", "계약", "특징주", "종목", "펀드",
    "ETF", "배당", "유가", "원유", "달러", "지수", "선물", "채권", "증권",
    "매수", "매도",
)
_ASCII_TOKEN_RE = re.compile(r"^[0-9A-Za-z][0-9A-Za-z&+.\- ]*$")


@lru_cache(maxsize=1024)
def _token_pattern(token: str):
    """토큰 하나의 매칭 정규식.

    · 영문/숫자 토큰 : 앞뒤 영문 경계 — 'AI' 가 'TRAIN' 에 걸리지 않고,
      'HBM' 은 'HBM4' 를 포함한다(숫자 접미 허용).
    · '이란'        : 앞에 한글 음절이 붙으면('-이란' 서술격 조사) 다른 단어이므로 차단,
      뒤에는 '이란전쟁'·'이란군' 같은 합성어를 허용하고 '이란성(雙)' 만 막는다.
    · 한 글자 한글    : 낱말 경계 필수(ex. '책' vs '책임').
    · 그 밖의 한글    : 부분 문자열 매칭(기존 동작 유지).
    """
    tok = (token or "").strip()
    if not tok:
        return None
    esc = re.escape(tok)
    if _ASCII_TOKEN_RE.match(tok) and re.search(r"[A-Za-z]", tok):
        return re.compile(rf"(?<![A-Za-z]){esc}(?![A-Za-z])", re.IGNORECASE)
    if tok in _COPULA_TOKENS:
        return re.compile(rf"(?<![{_HANGUL}]){esc}(?!성)")
    if len(tok) == 1:
        return re.compile(rf"(?<![{_HANGUL}]){esc}(?![{_HANGUL}])")
    return re.compile(esc)


def token_present(token: str, text: str) -> bool:
    """토큰이 단어 경계 규칙을 지켜 본문에 있는지."""
    if not token or not text:
        return False
    pattern = _token_pattern(str(token))
    return bool(pattern and pattern.search(text))


def _filter_lists(cfg: dict | None = None) -> tuple:
    """config 의 news_filter(없으면 기본값) → (노이즈 토큰, 경제 예외 토큰)."""
    conf = ((cfg or {}).get("news_filter") or {})
    noise = tuple(conf.get("noise_exclude") or NOISE_TOKENS) + tuple(NOISE_WORD_TOKENS)
    market = tuple(conf.get("market_context") or MARKET_CONTEXT_TOKENS)
    return noise, market


def is_noise_title(title: str, cfg: dict | None = None) -> bool:
    """비증시·사회/문화 노이즈 제목인지(경제·증시 신호가 함께 있으면 예외)."""
    text = (title or "").strip()
    if not text:
        return False
    noise, market = _filter_lists(cfg)
    if not any(token_present(t, text) for t in noise):
        return False
    return not any(token_present(t, text) for t in market)


def keyword_tokens(keyword: str) -> list:
    """'미국 이란 전쟁' → ['미국','이란','전쟁'] 같은 관련성 판정용 토큰."""
    return [t for t in re.split(r"\s+", keyword or "") if len(t) >= 2]


def default_context_tokens(label: str, tokens: list | None = None) -> list:
    for key in [label] + list(tokens or []):
        if key in DEFAULT_CONTEXT:
            return list(DEFAULT_CONTEXT[key])
    return []


def keyword_rule(label: str, entry: dict | None = None) -> dict:
    """키워드 하나의 판정 규칙 — 토큰·복합 맥락·차단 구절.

    require 를 명시하지 않은 여러 낱말 키워드는 모든 낱말이 함께 있어야 통과한다.
    """
    entry = dict(entry or {})
    explicit = [str(t) for t in (entry.get("require") or []) if str(t).strip()]
    tokens = explicit or [t for t in keyword_tokens(label) if t]
    context = [str(t) for t in (entry.get("context_any") or []) if str(t).strip()]
    if not context:
        context = default_context_tokens(label, tokens)
    block = [str(p) for p in (entry.get("block_phrases") or []) if str(p).strip()]
    for key in [label] + tokens:
        block.extend(TOKEN_BLOCK_PHRASES.get(key, ()))
    return {
        "label": label,
        "tokens": tokens,
        "context_any": context,
        "block_phrases": list(dict.fromkeys(block)),
        "require_all": not explicit and len(tokens) > 1,
    }


def _strip_block_phrases(text: str, rule: dict) -> str:
    for phrase in rule.get("block_phrases") or ():
        if phrase and phrase in text:
            text = text.replace(phrase, " ")
    return text


def keyword_matches(text: str, rule: dict | None) -> bool:
    """3중 필터 중 ①단어 경계 + ②복합 맥락 판정."""
    rule = rule or {}
    body = _strip_block_phrases((text or "").strip(), rule)
    if not body:
        return False
    tokens = [t for t in (rule.get("tokens") or []) if t]
    if tokens:
        hits = [token_present(t, body) for t in tokens]
        if rule.get("require_all"):
            if not all(hits):
                return False
        elif not any(hits):
            return False
    context = [t for t in (rule.get("context_any") or []) if t]
    if context and not any(token_present(t, body) for t in context):
        return False
    return True


def article_text(article: dict) -> str:
    return " ".join(str(article.get(k) or "") for k in ("title", "summary")
                    if article.get(k)).strip()


def article_passes(article, rule: dict | None, cfg: dict | None = None,
                   noise: bool = True) -> bool:
    """기사 한 건이 3중 필터를 통과하는지.

    article 은 dict(title/summary) 또는 문자열(제목)이다. 노이즈 판정은 제목만 본다
    (본문에 우연히 들어간 '결혼' 같은 낱말로 기사를 버리지 않기 위해).
    """
    if isinstance(article, str):
        title, text = article, article
    else:
        article = article or {}
        title = str(article.get("title") or "")
        text = article_text(article)
    if noise and is_noise_title(title, cfg):
        return False
    return keyword_matches(text, rule)


def filter_relevant(articles: list, tokens: list, context_any: list | None = None,
                    cfg: dict | None = None) -> list:
    """제목에 토큰이 없거나 노이즈·맥락 조건을 못 맞춘 기사는 검색 노이즈로 보고 제외한다."""
    tokens = [str(t) for t in (tokens or []) if str(t).strip()]
    if not tokens and not context_any:
        return articles
    block = []
    for t in tokens:
        block.extend(TOKEN_BLOCK_PHRASES.get(t, ()))
    rule = {
        "tokens": tokens,
        "context_any": list(context_any or default_context_tokens("", tokens)),
        "block_phrases": list(dict.fromkeys(block)),
        "require_all": False,
    }
    return [a for a in articles if article_passes(a, rule, cfg)]


def fetch_news_for(query: str, max_age_days: int = 10, limit: int = 30,
                   require_tokens: list | None = None,
                   context_any: list | None = None) -> list:
    params = urllib.parse.urlencode({
        "q": query, "hl": "ko", "gl": "KR", "ceid": "KR:ko",
    })
    xml_bytes = http_get(f"{GOOGLE_NEWS}?{params}", timeout=8, retries=1,
                         max_seconds=15)
    articles = parse_google_news_rss(xml_bytes, query, max_age_days * 2, limit * 3)
    if require_tokens or context_any:
        articles = filter_relevant(articles, require_tokens or [], context_any)
    return articles[:limit]


def is_fresh(fetched_at: str | None, minutes: int) -> bool:
    """직전 수집 시각이 minutes 이내인지 (429 회피용 저부하 캐시)."""
    if not fetched_at:
        return False
    try:
        dt = datetime.fromisoformat(fetched_at)
    except Exception:
        return False
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=KST)
    return (now_kst() - dt) < timedelta(minutes=minutes)


# --------------------------------------------------------------------------
# SAVE(세이브티커) 연동 — www.saveticker.com
#   · SAVE PICK / 로이터 / 관심 종목·키워드 뉴스와 경제 캘린더를 가져와
#     키워드 트레이스·분석 기업 뉴스에 "SAVE" 배지 기사로 합친다.
#   · saveticker.com 은 Cloudflare 봇 차단(403 "Just a moment…") 뒤에 있다.
#     GitHub Actions 같은 익명 요청은 대개 차단되므로,
#       1) 짧은 타임아웃으로 직접 조회를 한 번 시도하고
#       2) 실패하면 저장소에 커밋된 마지막 스냅샷(data/save_latest.json)을 그대로 쓰며
#       3) 스냅샷조차 없으면 "연동 불가" 상태를 남기고 조용히 넘어간다.
#     (수집 전체를 붙잡지 않는다 — SAVE는 부가 정보이고, 실패해도 앱은 계속 돈다)
#   · 스냅샷은 사용자의 로그인 브라우저 세션에서 갱신해 커밋한다.
# --------------------------------------------------------------------------
SAVE_SITE = "https://www.saveticker.com"
SAVE_PATH = os.path.join(DATA_DIR, "save_latest.json")
SAVE_TOP_URL = SAVE_SITE + "/api/news/top-stories"
SAVE_LIST_URL = SAVE_SITE + "/api/news/list"
SAVE_CALENDAR_URL = SAVE_SITE + "/api/calendar/events"
SAVE_TIMEOUT = _env_number("SAVE_TIMEOUT", 6)
SAVE_CALL_SECONDS = _env_number("SAVE_CALL_SECONDS", 8)
SAVE_MAX_ARTICLES = 40        # 한 번에 다룰 SAVE 기사 상한
SAVE_PER_BUCKET = 6           # 키워드·기업 버킷당 SAVE 기사 상한
SAVE_CALENDAR_DAYS = 14


def save_blocked_reason(head: bytes) -> str:
    """Cloudflare 챌린지 페이지인지 판별. 아니면 빈 문자열."""
    text = (head or b"").decode("utf-8", "replace")[:2048]
    lowered = text.lower()
    if "just a moment" in lowered or "cf_chl" in lowered or "challenge-platform" in lowered:
        return "Cloudflare 봇 차단(Just a moment) — 익명 요청으로는 접근 불가"
    return ""


def save_http_json(url: str, timeout: float | None = None) -> dict:
    """SAVE API GET. 차단/실패는 SaveFetchError 로 올린다(재시도 없음 — 빠르게 포기)."""
    timeout = SAVE_TIMEOUT if timeout is None else float(timeout)
    limit = min(SAVE_CALL_SECONDS, max(0.5, budget_left()))
    req = urllib.request.Request(url, headers={
        "User-Agent": UA,
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "ko-KR,ko;q=0.9,en;q=0.8",
        "Referer": SAVE_SITE + "/news",
    })
    try:
        body = _urlopen_read(req, min(timeout, limit), limit, url.split("?")[0])
    except urllib.error.HTTPError as e:  # noqa: PERF203
        try:
            head = e.read() or b""
        except Exception:  # noqa: BLE001
            head = b""
        reason = save_blocked_reason(head)
        raise SaveFetchError(reason or f"HTTP {e.code} ({url.split('?')[0]})") from None
    except Exception as e:  # noqa: BLE001 - 타임아웃·DNS·연결
        raise SaveFetchError(f"{type(e).__name__}: {e}") from None
    blocked = save_blocked_reason(body)
    if blocked:
        raise SaveFetchError(blocked)
    try:
        payload = json.loads(body.decode("utf-8", "replace"))
    except Exception as e:  # noqa: BLE001
        raise SaveFetchError(f"JSON 파싱 실패: {e}") from None
    if not isinstance(payload, dict):
        raise SaveFetchError("예상하지 못한 응답 형식")
    return payload


class SaveFetchError(RuntimeError):
    """SAVE 조회 실패(차단·타임아웃·형식 오류) — 스냅샷 폴백 신호."""


def save_symbols(cfg: dict) -> dict:
    """앱 종목코드 → SAVE 티커 심볼 (국내 005930 → 005930.KR, 해외 NVDA → NVDA)."""
    out = {}
    for code in (cfg.get("kr_stocks") or {}):
        out[code] = f"{code}.KR"
    for code in (cfg.get("us_stocks") or {}):
        out[code] = code
    return out


def save_keyword_rules(cfg: dict) -> list:
    """트레이스 키워드 → [(표시명, 3중 필터 규칙)].

    config 의 require·context_any 를 그대로 쓰고, 없으면 키워드 낱말과 기본 맥락을 쓴다.
    """
    rules = []
    for kw in (cfg.get("trace_keywords") or []):
        label = kw.get("keyword") or ""
        if label:
            rules.append((label, keyword_rule(label, kw)))
    return rules


def save_ticker_codes(cfg: dict) -> dict:
    """SAVE 티커 심볼 → 앱 종목코드. SK하이닉스 ADR(SKHY)은 국내 코드로 묶는다."""
    out = {sym: code for code, sym in save_symbols(cfg).items()}
    if "000660" in (cfg.get("kr_stocks") or {}):
        out.setdefault("SKHY", "000660")
    return out


def save_interests(cfg: dict) -> tuple:
    """SAVE 계정에 실제로 등록된 관심 키워드·종목.

    config.json 의 save 블록이 기준이고, 없으면 트레이스 키워드와 추적 종목으로 대체한다.
    (앱의 전체 추적 목록을 SAVE 관심 설정으로 보이게 하지 않기 위해 분리했다)
    """
    conf = cfg.get("save") or {}
    keywords = conf.get("keywords") or [k.get("keyword") for k in (cfg.get("trace_keywords") or [])]
    tickers = conf.get("tickers") or list(save_symbols(cfg).values())
    return ([k for k in keywords if k], [t for t in tickers if t])


def save_ticker_label(raw: dict) -> str:
    return str(raw.get("symbol") or "").upper()


def save_article_kind(raw: dict) -> str:
    """배지 문구: SAVE PICK(편집 기사) / SAVE(로이터 등 연동 기사)."""
    channel = (raw.get("channel") or "").lower()
    return "SAVE PICK" if channel == "save-pick" else "SAVE"


# SAVE API 가 영문 키로 주는 매체명 표기
SAVE_SOURCE_NAMES = {
    "reuters": "로이터",
    "bloomberg": "블룸버그",
    "ft": "파이낸셜타임스",
    "wsj": "WSJ",
    "cnbc": "CNBC",
    "ap": "AP",
    "nikkei": "닛케이",
}


def save_article_source(raw: dict) -> str:
    """화면에 보일 출처 — 매체명. SAVE 여부는 배지(SAVE/SAVE PICK)로 구분한다."""
    source = (raw.get("source") or "").strip()
    if not source:
        return "SAVE PICK" if (raw.get("channel") or "") == "save-pick" else "SAVE"
    return SAVE_SOURCE_NAMES.get(source.lower(), source)


def save_datetime(raw: dict) -> datetime | None:
    """created_at 은 UTC(Z) ISO 문자열 — KST datetime 으로 변환."""
    value = raw.get("created_at") or (raw.get("extra") or {}).get("source_created_at")
    if not value:
        return None
    text = str(value).replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(text)
    except Exception:  # noqa: BLE001
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(KST)


def parse_save_item(raw: dict, keyword_rules: list, ticker_codes: dict,
                    max_age_days: int = 10, now: datetime | None = None) -> dict | None:
    """SAVE API 기사 1건 → 앱 기사 형식(+origin/badge/keywords/companies)."""
    if not isinstance(raw, dict) or raw.get("is_deleted"):
        return None
    title = clean_title(str(raw.get("title") or ""))
    if not title:
        return None
    dt = save_datetime(raw)
    if dt is None:
        return None
    now = now or now_kst()
    if (now - dt) > timedelta(days=max_age_days):
        return None
    body = str(raw.get("content") or "")
    keywords = []
    for item in keyword_rules or []:
        if isinstance(item, (tuple, list)) and len(item) == 2:
            label, rule = item
        elif isinstance(item, dict):
            label, rule = item.get("label") or "", item
        else:
            label, rule = str(item or ""), {"tokens": [str(item or "")]}
        if label and article_passes({"title": title, "summary": body}, rule):
            keywords.append(label)
    companies = []
    for ticker in (raw.get("tickers") or []):
        code = ticker_codes.get(save_ticker_label(ticker))
        if code and code not in companies:
            companies.append(code)
    labels = [str(t.get("name")) for t in (raw.get("content_labels") or []) if t.get("name")]
    article = {
        "keyword": keywords[0] if keywords else "",
        "title": title,
        "source": save_article_source(raw),
        "url": f"{SAVE_SITE}/news/{raw.get('id')}" if raw.get("id") else SAVE_SITE + "/news",
        "date": dt.strftime("%Y-%m-%d"),
        "time": dt.strftime("%H:%M"),
        "published_at": iso_kst(dt),
        "origin": "SAVE",
        "badge": save_article_kind(raw),
        "labels": labels[:4],
        "view_count": raw.get("view_count"),
        "summary": _WS_RE.sub(" ", body).strip()[:200],
        "keywords": keywords,
        "companies": companies,
        "is_top_story": bool(raw.get("is_top_story")),
    }
    return article


def parse_save_news_payload(payload: dict, cfg: dict, now: datetime | None = None,
                            max_age_days: int = 10) -> list:
    """SAVE /api/news/list · /api/news/top-stories 응답 → 기사 목록(중복 제거)."""
    rules = save_keyword_rules(cfg)
    ticker_codes = save_ticker_codes(cfg)
    out, seen = [], set()
    for raw in (payload or {}).get("news_list") or []:
        art = parse_save_item(raw, rules, ticker_codes, max_age_days=max_age_days, now=now)
        if not art:
            continue
        key = title_key(art["title"])
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(art)
    out.sort(key=lambda a: a["published_at"], reverse=True)
    return out


def parse_save_calendar_payload(payload: dict, days: int = SAVE_CALENDAR_DAYS,
                                now: datetime | None = None) -> list:
    """경제 캘린더(미국·한국) → 화면용 이벤트 목록. event_date 는 사용자 현지(KST) 시각."""
    now = now or now_kst()
    horizon = now + timedelta(days=days)
    events = []
    for raw in (payload or {}).get("events") or []:
        value = raw.get("event_date")
        if not value:
            continue
        try:
            dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except Exception:  # noqa: BLE001
            continue
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=KST)
        dt = dt.astimezone(KST)
        if dt < now - timedelta(hours=6) or dt > horizon:
            continue
        note = ""
        for part in raw.get("content") or []:
            if isinstance(part, dict) and part.get("content"):
                note = _WS_RE.sub(" ", str(part["content"])).strip()
                break
        events.append({
            "id": str(raw.get("id") or ""),
            "title": _WS_RE.sub(" ", str(raw.get("title") or "")).strip(),
            "market": raw.get("market") or "",
            "date": dt.strftime("%Y-%m-%d"),
            "time": dt.strftime("%H:%M"),
            "datetime": iso_kst(dt),
            "note": note[:160],
            "importance": str(raw.get("title") or "").count("★"),
        })
    events.sort(key=lambda e: e["datetime"])
    return events


def build_save_snapshot(payloads: dict, cfg: dict, fetched_at: str,
                        now: datetime | None = None) -> dict:
    """수집한 원본 응답들 → 저장소에 커밋할 스냅샷(data/save_latest.json)."""
    now = now or now_kst()
    items, seen = [], set()
    for key in ("top", "list", "keywords", "tickers"):
        for art in parse_save_news_payload(payloads.get(key) or {}, cfg, now=now):
            k = title_key(art["title"])
            if not k or k in seen:
                continue
            seen.add(k)
            items.append(art)
    items.sort(key=lambda a: a["published_at"], reverse=True)
    items = items[:SAVE_MAX_ARTICLES]
    top_ids = {a["url"] for a in parse_save_news_payload(payloads.get("top") or {}, cfg, now=now)}
    top_stories = [a for a in items if a["url"] in top_ids][:10]
    return {
        "schema": 1,
        "source": "SAVE(세이브티커) saveticker.com",
        "fetched_at": fetched_at,
        "fetched_via": payloads.get("via") or "live",
        "interests": {
            "keywords": save_interests(cfg)[0],
            "tickers": save_interests(cfg)[1],
        },
        "count": len(items),
        "news": items,
        "top_stories": top_stories,
        "calendar": parse_save_calendar_payload(payloads.get("calendar") or {}, now=now),
        "endpoints": {
            "top_stories": SAVE_TOP_URL,
            "news_list": SAVE_LIST_URL,
            "calendar": SAVE_CALENDAR_URL,
        },
    }


def fetch_save_bundle(cfg: dict) -> dict:
    """SAVE API 4종 조회. 하나라도 차단되면 SaveFetchError 로 빠르게 실패한다."""
    keywords, symbols = save_interests(cfg)
    base = {"page": 1, "page_size": 20, "sort": "created_at_desc"}
    bundle = {}
    bundle["top"] = save_http_json(SAVE_TOP_URL)
    bundle["list"] = save_http_json(SAVE_LIST_URL + "?" + urllib.parse.urlencode(
        dict(base, keywords=",".join(keywords), tickers=",".join(symbols))))
    if keywords:
        bundle["keywords"] = save_http_json(SAVE_LIST_URL + "?" + urllib.parse.urlencode(
            dict(base, keywords=",".join(keywords))))
    if symbols:
        bundle["tickers"] = save_http_json(SAVE_LIST_URL + "?" + urllib.parse.urlencode(
            dict(base, tickers=",".join(symbols))))
    today = now_kst()
    bundle["calendar"] = save_http_json(SAVE_CALENDAR_URL + "?" + urllib.parse.urlencode({
        "start_date": today.strftime("%Y-%m-%d"),
        "end_date": (today + timedelta(days=SAVE_CALENDAR_DAYS)).strftime("%Y-%m-%d"),
    }))
    bundle["via"] = "live"
    return bundle


def collect_save(cfg: dict, previous: dict | None) -> dict:
    """SAVE 스냅샷 확보: 직접 조회 → 실패 시 직전 스냅샷(캐시) 유지 → 없으면 연동 불가.

    어떤 경우에도 예외를 밖으로 던지지 않는다(부가 정보가 본 수집을 막지 않게).
    """
    previous = previous or {}
    conf = cfg.get("save") if isinstance(cfg.get("save"), dict) else {}
    if cfg.get("save_enabled") is False or conf.get("enabled") is False:
        return {"schema": 1, "status": "disabled", "news": [], "top_stories": [],
                "calendar": [], "count": 0, "errors": []}
    try:
        snapshot = build_save_snapshot(fetch_save_bundle(cfg), cfg, iso_kst(now_kst()))
        snapshot["status"] = "live"
        snapshot["errors"] = []
        log(f"[save] 실시간 수집 성공: 기사 {snapshot['count']}건 · 일정 {len(snapshot['calendar'])}건")
        return snapshot
    except SaveFetchError as e:
        reason = str(e)
    except Exception as e:  # noqa: BLE001 - 어떤 실패도 여기서 멈춘다
        reason = f"{type(e).__name__}: {e}"
    log(f"[save] 직접 조회 불가({reason}) → 저장된 스냅샷 사용")
    if previous.get("news"):
        cached = json.loads(json.dumps(previous))
        cached["status"] = "cached"
        cached["stale"] = True
        cached["errors"] = [f"SAVE 직접 조회 불가: {reason}"]
        cached["carried_at"] = iso_kst(now_kst())
        return cached
    return {"schema": 1, "status": "unavailable", "news": [], "top_stories": [],
            "calendar": [], "count": 0, "errors": [f"SAVE 직접 조회 불가: {reason}"]}


def merge_save_news(news: dict, snapshot: dict, cfg: dict | None = None,
                    max_age_days: int = 10) -> dict:
    """SAVE 기사를 키워드·기업 뉴스 버킷에 합치고 news['save'] 요약을 만든다.

    · 같은 제목(정규화) 기사는 한 번만 넣는다(구글 뉴스와 중복 방지)
    · 버킷이 비어 있어도(구글 수집 실패) SAVE 기사로 버킷을 만든다
    · 버킷당 SAVE 기사는 SAVE_PER_BUCKET 건까지만(최신순)
    """
    cfg = cfg or {}
    snapshot = snapshot or {}
    items = snapshot.get("news") or []
    cutoff = now_kst() - timedelta(days=max_age_days)
    added = {"keywords": 0, "companies": 0}
    # 스냅샷에 저장된 예전 키워드 태그를 그대로 믿지 않고 3중 필터로 다시 판정한다
    rules = dict(save_keyword_rules(cfg))
    if items:
        keyword_buckets = news.setdefault("keywords", {})
        company_buckets = news.setdefault("companies", {})
        for kw in (cfg.get("trace_keywords") or []):
            label = kw.get("keyword")
            if label and label not in keyword_buckets:
                keyword_buckets[label] = {"query": kw.get("query") or label, "articles": [],
                                          "keyword": label, "id": kw.get("id")}
        for code, name in list((cfg.get("kr_stocks") or {}).items()) + \
                [(c, c) for c in (cfg.get("us_stocks") or {})]:
            if code not in company_buckets:
                company_buckets[code] = {"query": f"{name} 실적", "articles": [], "code": code}
        for art in items:
            try:
                pub = datetime.fromisoformat(str(art.get("published_at")).replace("Z", "+00:00"))
            except Exception:  # noqa: BLE001
                pub = None
            if pub is not None and pub.tzinfo is not None and pub.astimezone(KST) < cutoff:
                continue
            targets = list(art.get("keywords") or [])
            if art.get("keyword"):
                targets.append(art["keyword"])
            for label in dict.fromkeys(targets):
                bucket = keyword_buckets.get(label)
                if not bucket:
                    continue
                rule = rules.get(label)
                if rule is None or not article_passes(art, rule, cfg):
                    continue
                merged = dict(art)
                merged["keyword"] = label
                if _bucket_put(bucket, merged):
                    added["keywords"] += 1
            for code in art.get("companies") or []:
                bucket = company_buckets.get(code)
                if not bucket:
                    continue
                merged = dict(art)
                merged["keyword"] = bucket.get("query") or code
                if _bucket_put(bucket, merged):
                    added["companies"] += 1
    news["save"] = {
        "status": snapshot.get("status") or "unavailable",
        "stale": bool(snapshot.get("stale")) or (snapshot.get("status") == "cached"),
        "fetched_at": snapshot.get("fetched_at"),
        "fetched_via": snapshot.get("fetched_via"),
        "count": len(items),
        "top_stories": snapshot.get("top_stories") or [],
        "calendar": snapshot.get("calendar") or [],
        "interests": snapshot.get("interests") or {},
        "added": added,
        "errors": snapshot.get("errors") or [],
        "site": SAVE_SITE,
    }
    return news


def _bucket_put(bucket: dict, article: dict) -> bool:
    """버킷에 SAVE 기사를 넣는다. 같은 제목이 이미 있으면:

    · 기존 기사도 SAVE 것이면 내용을 최신 스냅샷 값으로 갱신한다(출처·배지·시각 등)
    · 구글 뉴스 기사면 중복이므로 건너뛴다
    반환값은 '새로 추가했는가'.
    """
    key = title_key(article.get("title") or "")
    articles = bucket.setdefault("articles", [])
    for idx, a in enumerate(articles):
        if key and title_key(a.get("title") or "") == key:
            if a.get("origin") == "SAVE":
                articles[idx] = dict(article, keyword=a.get("keyword") or article.get("keyword"))
            return False
    if sum(1 for a in articles if a.get("origin") == "SAVE") >= SAVE_PER_BUCKET:
        return False
    articles.insert(0, article)
    return True


# --------------------------------------------------------------------------
# briefing (실제 수치로만 문장 생성 — 추정/가짜 값 없음)
# --------------------------------------------------------------------------
def fmt_num(v, digits=2):
    if v is None:
        return "-"
    if digits == 0:
        return f"{v:,.0f}"
    return f"{v:,.{digits}f}"


def fmt_pct(v):
    if v is None:
        return "-"
    sign = "+" if v > 0 else ""
    return f"{sign}{v:.2f}%"


def build_briefing(market: dict, stock_map: dict, news: dict) -> dict:
    indices = market.get("indices") or {}
    rates = market.get("rates") or {}
    fx = market.get("fx") or {}
    stocks = market.get("stocks") or {}

    def idx_line(key, label):
        q = indices.get(key) or {}
        if q.get("price") is None:
            return None
        return f"{label} {fmt_num(q['price'])} ({fmt_pct(q.get('pct'))})"

    us_line = ", ".join(x for x in [
        idx_line("DOW", "다우"), idx_line("NASDAQ", "나스닥"),
        idx_line("SP500", "S&P500"), idx_line("SOX", "SOX"),
    ] if x)
    kr_line = ", ".join(x for x in [
        idx_line("KOSPI", "코스피"), idx_line("KOSDAQ", "코스닥"),
    ] if x)

    headline = ""
    if us_line:
        headline = f"미국 증시 마감: {us_line}"
        if kr_line:
            headline += f" / {kr_line}"

    def stock_moves(keys):
        rows = []
        for k in keys:
            q = stocks.get(k) or {}
            if q.get("pct") is None:
                continue
            rows.append((q.get("name") or k, q["pct"], q.get("price")))
        return rows

    us_rows = stock_moves([k for k in stock_map.get("us", [])])
    kr_rows = stock_moves([k for k in stock_map.get("kr", [])])
    worst_us = min(us_rows, key=lambda r: r[1]) if us_rows else None
    worst_kr = min(kr_rows, key=lambda r: r[1]) if kr_rows else None
    best_us = max(us_rows, key=lambda r: r[1]) if us_rows else None

    checkpoints = []
    sox = indices.get("SOX") or {}
    if sox.get("price") is not None:
        detail = f"필라델피아 반도체 지수 {fmt_num(sox['price'])} ({fmt_pct(sox.get('pct'))})"
        if worst_us:
            detail += f", 추적 기술주 중 낙폭 최대는 {worst_us[0]} {fmt_pct(worst_us[1])}"
        checkpoints.append({"title": "반도체·기술주 흐름", "detail": detail + "."})
    if best_us:
        checkpoints.append({
            "title": "상대 강세 종목",
            "detail": f"추적 기술주 중 {best_us[0]} {fmt_pct(best_us[1])} "
                      f"({fmt_num(best_us[2])}) 가 가장 견조했습니다.",
        })
    y10 = rates.get("US10Y") or {}
    fxq = fx.get("USDKRW") or {}
    if y10.get("price") is not None or fxq.get("price") is not None:
        parts = []
        if y10.get("price") is not None:
            bp = y10.get("bp")
            parts.append(f"미 10년물 {fmt_num(y10['price'], 3)}%"
                         + (f" ({bp:+.2f}bp)" if bp is not None else ""))
        y2 = rates.get("US2Y") or {}
        if y2.get("price") is not None:
            parts.append(f"미 2년물 {fmt_num(y2['price'], 3)}%")
        if fxq.get("price") is not None:
            parts.append(f"원/달러 {fmt_num(fxq['price'])}원 ({fmt_pct(fxq.get('pct'))})")
        checkpoints.append({"title": "금리·환율", "detail": ", ".join(parts) + "."})
    if kr_rows:
        ext_parts = []
        for k in stock_map.get("kr", []):
            q = stocks.get(k) or {}
            ext = q.get("extended") or {}
            if ext.get("price") is not None and ext.get("pct") is not None:
                ext_parts.append(f"{q.get('name')} {fmt_pct(ext['pct'])}")
        moved = [r for r in kr_rows if r[1]]
        detail_parts = []
        if moved:
            detail_parts.append(
                f"국내 추적 종목 낙폭 최대는 {min(moved, key=lambda r: r[1])[0]} "
                f"{fmt_pct(min(moved, key=lambda r: r[1])[1])}")
        if ext_parts:
            detail_parts.append("장전 시간외 " + ", ".join(ext_parts[:3]))
        if detail_parts:
            checkpoints.append({"title": "국내 증시", "detail": ", ".join(detail_parts) + "."})

    sector_news = []
    for sector in news.get("sectors", []):
        arts = sector.get("articles") or []
        if not arts:
            continue
        top = arts[0]
        sector_news.append({
            "tag": sector.get("tag"),
            "text": top["title"],
            "date": top["date"],
            "source": top["source"],
            "url": top["url"],
        })

    return {
        "headline": headline,
        "checkpoints": checkpoints,
        "sector_news": sector_news[:3],
    }


# --------------------------------------------------------------------------
# 전종목 검색·등록 (종목 유니버스)
# --------------------------------------------------------------------------
def parse_market_list_item(item: dict) -> dict | None:
    """네이버 전종목 시세 목록(m.stock.naver.com/api/stocks/marketValue)의 한 행을 정리한다."""
    code = str(item.get("itemCode") or "").strip()
    if not code or not code.isdigit():
        return None
    name = str(item.get("stockName") or "").strip()
    if not name:
        return None
    market = (item.get("stockExchangeType") or {}).get("name")
    if not market:
        market = "KOSDAQ" if str(item.get("sosok")) == "1" else "KOSPI"
    return {
        "code": code,
        "name": name,
        "market": str(market).upper(),
        "price": num(item.get("closePrice")),
        "pct": num(item.get("fluctuationsRatio")),
        "mcap_eok": num(item.get("marketValue")),   # 억원 단위
        "volume": num(item.get("accumulatedTradingVolume")),
        "as_of": item.get("localTradedAt"),
        "status": item.get("marketStatus"),
    }


def fetch_kr_universe(page_size: int = 100, max_pages: int = 60,
                      budget_seconds: float | None = None) -> dict:
    """코스피·코스닥 전 종목의 이름·코드·현재가·등락률·시가총액을 수집한다."""
    seconds = UNIVERSE_BUDGET_SECONDS if budget_seconds is None else float(budget_seconds)
    deadline = time.monotonic() + max(1.0, min(seconds, budget_left()))
    out = {}
    for market in ("KOSPI", "KOSDAQ"):
        page = 1
        while page <= max_pages:
            if time.monotonic() >= deadline:
                log(f"[universe] {market} 목록 수집 시간 예산({seconds:.0f}초) 소진 — page {page} 에서 중단")
                break
            url = f"{NAVER_LIST}/{market}?page={page}&pageSize={page_size}"
            payload = get_json(url, timeout=8, max_seconds=12)
            rows = payload.get("stocks") or []
            if not rows:
                break
            for item in rows:
                entry = parse_market_list_item(item)
                if entry:
                    out[entry["code"]] = entry
            total = num(payload.get("totalCount")) or 0
            if total and page * page_size >= total:
                break
            page += 1
        log(f"[universe] {market} 목록 {len(out)}종목 수집(마지막 page {page})")
    return out


def load_us_symbols() -> list:
    data = load_json(US_SYMBOLS_PATH, {})
    symbols = data.get("symbols") if isinstance(data, dict) else None
    return [str(s).upper() for s in (symbols or [])]


def parse_world_list_item(item: dict) -> dict | None:
    """네이버 해외 종목 polling 응답 한 건을 정리한다 (reutersCode의 접미사 제거)."""
    rcode = str(item.get("reutersCode") or "").strip()
    if not rcode:
        return None
    symbol = rcode.split(".")[0].upper()
    quote = parse_naver_quote(item, "USD")
    exchange = (item.get("stockExchangeType") or {}).get("name") or "US"
    return {
        "symbol": symbol,
        "reuters": rcode,
        "name": quote.get("name") or symbol,
        "market": str(exchange).upper(),
        "price": quote.get("price"),
        "pct": quote.get("pct"),
        "mcap": quote.get("market_value"),
        "volume": quote.get("volume"),
        "as_of": quote.get("as_of"),
        "status": quote.get("market_status"),
    }


def fetch_us_universe(symbols, chunk: int = 350) -> dict:
    """미국 종목은 네이버 해외종목 코드 접미사(.O/.K, 접미사 없음)를 모두 시도해 찾는다."""
    if not symbols:
        return {}
    wanted = set(s.upper() for s in symbols)
    codes = []
    for s in symbols:
        s = s.upper()
        codes.extend([s, s + ".O", s + ".K"])
    out = {}
    for i in range(0, len(codes), chunk):
        if budget_left() <= 1:
            log("[universe] 미국 종목 수집 시간 예산 소진 — 남은 구간 건너뜀")
            break
        url = f"{NAVER_POLL}/worldstock/stock/" + ",".join(codes[i:i + chunk])
        payload = get_json(url, timeout=10, max_seconds=15)
        for item in payload.get("datas", []):
            entry = parse_world_list_item(item)
            if not entry or entry["symbol"] not in wanted:
                continue
            # 접미사 없는 코드(정확히 일치)를 우선 채택
            prev = out.get(entry["symbol"])
            if prev is None or (prev.get("reuters") != entry["symbol"] and entry["reuters"] == entry["symbol"]):
                out[entry["symbol"]] = entry
    return out


def parse_daum_fundamental(data: dict) -> dict | None:
    """Daum 금융 종목 요약에서 PER/PBR/EPS/BPS를 뽑는다."""
    if not isinstance(data, dict) or not data.get("tradePrice"):
        return None
    return {
        "per": num(data.get("per")),
        "pbr": num(data.get("pbr")),
        "eps": num(data.get("eps")),
        "bps": num(data.get("bps")),
        "cap": num(data.get("marketCap")),
        "sector": data.get("sectorName"),
    }


def fetch_daum_fundamental(code: str, timeout: float | None = None) -> dict | None:
    timeout = HTTP_TIMEOUT if timeout is None else float(timeout)
    req = urllib.request.Request(
        f"{DAUM_QUOTE}{code}?summary=false",
        headers={"User-Agent": UA, "Referer": "https://finance.daum.net/"})

    def _do():
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read(MAX_RESPONSE_BYTES).decode("utf-8", "replace"))

    data = run_with_timeout(_do, timeout + 2.0, label=f"daum:{code}")
    return parse_daum_fundamental(data)


def refresh_fundamentals(codes, previous: dict, today: str,
                         workers: int = 8, budget: int | None = None,
                         budget_seconds: float | None = None) -> dict:
    """시가총액 상위 국내 종목의 PER/PBR/EPS/BPS를 하루 1회 갱신한다.

    previous: 이전 stock_fundamentals.json ({"date": ..., "items": {code: {...}}})
    시간 예산을 넘기면 남은 종목은 다음 실행에서 이어 받는다(complete=False).
    """
    from concurrent.futures import (ThreadPoolExecutor, TimeoutError as FutureTimeout,
                                    as_completed)

    prev_items = dict(previous.get("items") or {})
    targets = [c for c in codes if (prev_items.get(c) or {}).get("d") != today]
    if budget is not None:
        targets = targets[:budget]
    if not targets:
        return {"date": today, "items": prev_items, "complete": True}

    seconds = FUND_BUDGET_SECONDS if budget_seconds is None else float(budget_seconds)
    deadline = time.monotonic() + max(1.0, min(seconds, budget_left()))

    def one(code):
        if time.monotonic() >= deadline:
            return code, None
        for attempt in range(2):
            try:
                f = fetch_daum_fundamental(code)
                if f is None:
                    return code, None
                f["d"] = today
                return code, f
            except Exception:  # noqa: BLE001 - 개별 종목 실패는 전체를 멈추지 않는다
                if attempt == 0 and time.monotonic() < deadline:
                    time.sleep(0.4)
        return code, None

    done = 0
    pool = ThreadPoolExecutor(max_workers=workers)
    try:
        futures = {pool.submit(one, code): code for code in targets}
        for fut in as_completed(futures,
                                timeout=max(0.5, deadline - time.monotonic())):
            try:
                code, f = fut.result()
            except Exception:  # noqa: BLE001
                continue
            done += 1
            if f:
                prev_items[code] = f
    except FutureTimeout:
        log(f"[universe] 재무지표 수집 시간 예산({seconds:.0f}초) 소진 — 나머지는 다음 실행에서")
    finally:
        pool.shutdown(wait=False, cancel_futures=True)

    complete = all((prev_items.get(c) or {}).get("d") == today for c in codes)
    log(f"[universe] 재무지표 갱신 {done}건 (시도 {len(targets)}, 오늘 완료={complete})")
    return {"date": today, "items": prev_items, "complete": complete}


def build_universe(kr: dict, us: dict, funds: dict, us_funds: dict | None = None) -> dict:
    """앱이 바로 쓰는 검색·시세 인덱스를 만든다.

    kr[code] = [이름, 시장, 현재가, 등락률, 시가총액(억원), PER, PBR, EPS, BPS]
    us[sym]  = [이름, 거래소, 현재가, 등락률, 시가총액(USD), PER, PBR, EPS, BPS]
    """
    fund_items = funds.get("items") or {}
    kr_rows = {}
    as_of_kr = None
    status_kr = None
    for code, e in kr.items():
        f = fund_items.get(code) or {}
        kr_rows[code] = [
            e["name"], e["market"], e.get("price"), e.get("pct"),
            int(e["mcap_eok"]) if e.get("mcap_eok") else None,
            _round(f.get("per")), _round(f.get("pbr")),
            _round(f.get("eps")), _round(f.get("bps")),
        ]
        if e.get("as_of") and (as_of_kr is None or str(e["as_of"]) > as_of_kr):
            as_of_kr = str(e["as_of"])
        if e.get("status") and not status_kr:
            status_kr = e["status"]

    us_rows = {}
    as_of_us = None
    status_us = None
    us_fund_items = (us_funds or {}).get("items") or {}
    for sym, e in us.items():
        f = us_fund_items.get(sym) or {}
        eps = f.get("eps")
        pbr = f.get("pbr")
        price = e.get("price")
        per = None
        if eps and eps > 0 and price:
            per = round(float(price) / float(eps), 2)
        bps = None
        if pbr and pbr > 0 and price:
            bps = round(float(price) / float(pbr), 2)
        us_rows[sym] = [
            e["name"], e["market"], price, e.get("pct"),
            int(e["mcap"]) if e.get("mcap") else None,
            per, _round(pbr), _round(eps), _round(bps),
        ]
        if e.get("as_of") and (as_of_us is None or str(e["as_of"]) > as_of_us):
            as_of_us = str(e["as_of"])
        if e.get("status") and not status_us:
            status_us = e["status"]

    return {
        "schema": 1,
        "source": "네이버 금융 전종목 시세 + Daum 금융 재무지표",
        "as_of": {"kr": as_of_kr, "us": as_of_us},
        "status": {"kr": status_kr, "us": status_us},
        "counts": {"kr": len(kr_rows), "us": len(us_rows), "fund": len(fund_items),
                   "us_fund": len(us_fund_items)},
        "fund_date": funds.get("date"),
        "kr": dict(sorted(kr_rows.items())),
        "us": dict(sorted(us_rows.items())),
    }


def _round(v, digits: int = 2):
    if v is None:
        return None
    try:
        return round(float(v), digits)
    except (TypeError, ValueError):
        return None


# --------------------------------------------------------------------------
# 기업 뉴스 (임의 종목 카드용·회전 수집)
# --------------------------------------------------------------------------
def company_news_targets(universe: dict) -> dict:
    """뉴스를 미리 모아둘 종목: 국내 시가총액 상위 + 미국 주요 종목. {code: 검색어}"""
    kr = universe.get("kr") or {}
    us = universe.get("us") or {}
    ranked = sorted(kr.items(), key=lambda kv: (-(kv[1][4] or 0), kv[0]))
    targets = {}
    for code, row in ranked[:COMPANY_NEWS_KR_LIMIT]:
        targets[code] = row[0]
    us_ranked = sorted(us.items(), key=lambda kv: (-(kv[1][4] or 0), kv[0]))
    for sym, row in us_ranked[:COMPANY_NEWS_US_LIMIT]:
        targets[sym] = (row[0] or sym).split("(")[0].strip() or sym
    return targets


def select_company_news_targets(targets: dict, previous: dict, now=None) -> list:
    """오래된 순으로 이번 실행에 수집할 종목을 고른다."""
    now = now or now_kst()
    prev = previous.get("companies") or {}
    pending = []
    for code in targets:
        entry = prev.get(code) or {}
        fetched = entry.get("fetched_at")
        fresh = False
        if fetched:
            try:
                dt = datetime.fromisoformat(str(fetched))
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=KST)
                fresh = (now - dt) < timedelta(hours=COMPANY_NEWS_REUSE_HOURS)
            except ValueError:
                fresh = False
        if not fresh or not entry.get("articles"):
            pending.append((fetched or "", code))
    pending.sort()
    return [code for _, code in pending[:COMPANY_NEWS_PER_RUN]]


def collect_company_news(universe: dict, previous: dict, force: bool = False) -> dict:
    """카드 뉴스용 기업별 기사를 회전 수집한다. 실패한 종목은 직전 기사를 유지한다."""
    targets = company_news_targets(universe)
    prev_companies = dict(previous.get("companies") or {})
    selected = list(targets.keys()) if force else select_company_news_targets(targets, previous)
    errors = []
    fetched = 0
    deadline = time.monotonic() + max(1.0, min(NEWS_BUDGET_SECONDS, budget_left()))
    for code in selected:
        if time.monotonic() >= deadline:
            log("[company-news] 시간 예산 소진 — 남은 종목은 다음 실행에서")
            errors.append("기업 뉴스 시간 예산 소진 — 남은 종목 다음 실행")
            break
        name = targets.get(code) or code
        try:
            arts = fetch_news_for(name, max_age_days=10, limit=8,
                                  require_tokens=keyword_tokens(name))
        except Exception as e:
            log(f"[warn] {name} 뉴스 수집 실패: {e}")
            errors.append(f"{name} 뉴스 수집 실패: {e}")
            continue
        _pace_sleep(0.4, deadline)
        fetched += 1
        prev_companies[code] = {
            "name": name,
            "fetched_at": iso_kst(now_kst()),
            "articles": arts[:4],
        }
    for code in list(prev_companies.keys()):
        if code not in targets:
            prev_companies.pop(code, None)
    return {"companies": prev_companies, "fetched": fetched, "errors": errors}


def parse_us_fundamentals(summary: dict, quarter: dict) -> dict | None:
    """네이버 해외종목 재무요약에서 TTM EPS와 PBR을 뽑는다."""
    eps = None
    chart = (summary or {}).get("chartEps") or {}
    columns = chart.get("columns") or []
    if len(columns) >= 2 and columns[1] and str(columns[1][0]).upper() == "EPS":
        values = [num(v) for v in columns[1][1:]]
        values = [v for v in values if v is not None]
        if len(values) >= 4:
            eps = round(sum(values[-4:]), 4)
    pbr = None
    for row in (quarter or {}).get("rowList") or []:
        if str(row.get("title")).upper() != "PBR":
            continue
        cols = row.get("columns") or {}
        for key in sorted(cols.keys()):
            value = num((cols.get(key) or {}).get("value"))
            if value is not None:
                pbr = value
        break
    if eps is None and pbr is None:
        return None
    return {"eps": eps, "pbr": pbr}


def fetch_us_fundamental(reuters_code: str, timeout: float | None = None) -> dict | None:
    timeout = HTTP_TIMEOUT if timeout is None else float(timeout)
    base = f"https://api.stock.naver.com/stock/{reuters_code}"
    try:
        summary = get_json(f"{base}/finance/summary", timeout=timeout, max_seconds=timeout + 3)
    except Exception:
        summary = {}
    try:
        quarter = get_json(f"{base}/finance/quarter", timeout=timeout, max_seconds=timeout + 3)
    except Exception:
        quarter = {}
    return parse_us_fundamentals(summary, quarter)


def refresh_us_fundamentals(us: dict, previous: dict, today: str,
                            workers: int = 6, limit: int = US_FUND_LIMIT,
                            budget_seconds: float | None = None) -> dict:
    """시가총액 상위 미국 종목의 TTM EPS·PBR을 하루 1회 갱신한다."""
    from concurrent.futures import (ThreadPoolExecutor, TimeoutError as FutureTimeout,
                                    as_completed)

    prev_items = dict(previous.get("items") or {})
    ranked = sorted(us.items(), key=lambda kv: (-(kv[1].get("mcap") or 0), kv[0]))[:limit]
    targets = [(sym, e.get("reuters") or sym) for sym, e in ranked
               if (prev_items.get(sym) or {}).get("d") != today]
    if not targets:
        return {"date": today, "items": prev_items, "complete": True}

    seconds = FUND_BUDGET_SECONDS if budget_seconds is None else float(budget_seconds)
    deadline = time.monotonic() + max(1.0, min(seconds, budget_left()))

    def one(item):
        sym, reuters = item
        if time.monotonic() >= deadline:
            return sym, None
        try:
            f = fetch_us_fundamental(reuters)
            if f:
                f["d"] = today
            return sym, f
        except Exception:  # noqa: BLE001
            return sym, None

    done = 0
    pool = ThreadPoolExecutor(max_workers=workers)
    try:
        futures = {pool.submit(one, item): item[0] for item in targets}
        for fut in as_completed(futures,
                                timeout=max(0.5, deadline - time.monotonic())):
            try:
                sym, f = fut.result()
            except Exception:  # noqa: BLE001
                continue
            done += 1
            if f:
                prev_items[sym] = f
    except FutureTimeout:
        log(f"[universe] 미국 재무지표 시간 예산({seconds:.0f}초) 소진 — 나머지는 다음 실행에서")
    finally:
        pool.shutdown(wait=False, cancel_futures=True)

    complete = all((prev_items.get(s) or {}).get("d") == today for s, _ in targets)
    log(f"[universe] 미국 재무지표 갱신 {done}건 (시도 {len(targets)}, 오늘 완료={complete})")
    return {"date": today, "items": prev_items, "complete": complete}


def collect_universe(fund_state: dict, us_fund_state: dict, prev_universe: dict) -> tuple:
    """전종목 검색 인덱스 + 재무지표를 갱신한다. (universe, fund_state, us_fund_state) 반환."""
    errors = []
    try:
        kr = fetch_kr_universe()
    except Exception as e:
        kr = {}
        errors.append(f"국내 전종목 수집 실패: {e}")
    if not kr:
        # 실패 시 직전 파일 유지
        prev_kr = prev_universe.get("kr") or {}
        kr = {code: {"code": code, "name": r[0], "market": r[1], "price": r[2], "pct": r[3],
                     "mcap_eok": r[4]} for code, r in prev_kr.items()}
        if prev_kr:
            errors.append("직전 유니버스로 유지")

    try:
        us = fetch_us_universe(load_us_symbols())
    except Exception as e:
        us = {}
        errors.append(f"해외 종목 수집 실패: {e}")
    if not us:
        prev_us = prev_universe.get("us") or {}
        for sym, row in prev_us.items():
            us[sym] = {"symbol": sym, "name": row[0], "market": row[1], "price": row[2],
                       "pct": row[3], "mcap": row[4], "as_of": None, "status": None}
        if prev_us:
            errors.append("직전 미국 종목 시세로 유지")

    today = now_kst().strftime("%Y-%m-%d")
    if kr and now_kst().hour >= UNIVERSE_FUND_MIN_HOUR and (
            fund_state.get("date") != today or fund_state.get("complete") is False):
        # 시가총액 상위 종목만 재무지표 대상으로 삼는다
        ranked = sorted(kr.items(), key=lambda kv: (-(kv[1].get("mcap_eok") or 0), kv[0]))
        codes = [c for c, e in ranked[:UNIVERSE_FUND_LIMIT]]
        try:
            fund_state = refresh_fundamentals(codes, fund_state, today)
        except Exception as e:
            errors.append(f"재무지표 갱신 실패: {e}")

    if us and now_kst().hour >= UNIVERSE_FUND_MIN_HOUR and (
            us_fund_state.get("date") != today or us_fund_state.get("complete") is False):
        try:
            us_fund_state = refresh_us_fundamentals(us, us_fund_state, today)
        except Exception as e:
            errors.append(f"미국 재무지표 갱신 실패: {e}")

    universe = build_universe(kr, us, fund_state, us_fund_state)
    universe["errors"] = errors
    return universe, fund_state, us_fund_state


# --------------------------------------------------------------------------
# collection
# --------------------------------------------------------------------------
def load_json(path, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def load_config() -> dict:
    cfg = json.loads(json.dumps(DEFAULT_CONFIG))
    if os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH, encoding="utf-8") as f:
                user_cfg = json.load(f)
            for k, v in user_cfg.items():
                cfg[k] = v
        except Exception as e:
            print(f"[warn] config.json 파싱 실패: {e}", file=sys.stderr)
    return cfg


def collect_market(cfg: dict, previous: dict) -> dict:
    errors = []
    prev_stocks = previous.get("stocks") or {}
    market = {"indices": {}, "rates": {}, "fx": {}, "stocks": {}}

    kr_codes = list(cfg["kr_stocks"].keys())
    name_map = dict(cfg["kr_stocks"])
    name_map.update({k: k for k in cfg["us_stocks"].keys()})

    try:
        market["stocks"].update(fetch_domestic_stocks(kr_codes))
    except Exception as e:
        errors.append(f"국내 주가 수집 실패: {e}")
        log(f"[warn] 국내 주가 수집 실패: {e}")

    try:
        market["stocks"].update(fetch_world_stocks(cfg["us_stocks"]))
    except Exception as e:
        errors.append(f"해외 주가 수집 실패: {e}")
        log(f"[warn] 해외 주가 수집 실패: {e}")

    try:
        market["indices"].update(fetch_domestic_index(cfg["kr_indices"]))
    except Exception as e:
        errors.append(f"국내 지수 수집 실패: {e}")
        log(f"[warn] 국내 지수 수집 실패: {e}")

    try:
        market["indices"].update(fetch_world_index(cfg["us_indices"]))
    except Exception as e:
        errors.append(f"해외 지수 수집 실패: {e}")
        log(f"[warn] 해외 지수 수집 실패: {e}")

    try:
        market["rates"] = fetch_rates(cfg["rates"])
    except Exception as e:  # noqa: BLE001
        market["rates"] = {}
        errors.append(f"금리 수집 실패: {e}")
        log(f"[warn] 금리 수집 실패: {e}")
    if not market["rates"]:
        errors.append("금리 수집 실패")
        market["rates"] = previous.get("market", {}).get("rates") or {}

    try:
        market["fx"] = fetch_fx(cfg["fx"])
    except Exception as e:  # noqa: BLE001
        market["fx"] = {}
        errors.append(f"환율 수집 실패: {e}")
        log(f"[warn] 환율 수집 실패: {e}")
    if not market["fx"]:
        errors.append("환율 수집 실패")
        market["fx"] = previous.get("market", {}).get("fx") or {}

    # 기준금리(정책금리): 공식 소스 실시간 조회 + 실패 시 직전 성공값 유지
    policy_rates, pr_errors = collect_policy_rates(previous)
    market["policy_rates"] = policy_rates
    errors.extend(pr_errors)

    # 실패한 종목은 직전 값 유지 + stale 표시
    for code, name in name_map.items():
        if code not in market["stocks"]:
            old = prev_stocks.get(code)
            if old:
                old = dict(old)
                old["stale"] = True
                market["stocks"][code] = old
                errors.append(f"{name}({code}) 시세 유지(직전값)")

    kr_session = None
    us_session = None
    for code in kr_codes:
        q = market["stocks"].get(code) or {}
        if q.get("market_status"):
            kr_session = q["market_status"]
            break
    for code in cfg["us_stocks"]:
        q = market["stocks"].get(code) or {}
        if q.get("market_status"):
            us_session = q["market_status"]
            break

    market["session"] = {"kr": kr_session, "us": us_session}
    market["errors"] = errors
    return market


def _pace_sleep(seconds: float, deadline: float | None = None) -> None:
    """요청 사이 간격. 남은 예산보다 길게 잠들지 않는다."""
    if deadline is not None:
        seconds = min(seconds, max(0.0, deadline - time.monotonic()))
    if seconds > 0:
        time.sleep(seconds)


def _refresh_entry(prev, query, reuse, max_age, limit, errors, label, force, state,
                   require_tokens=None, context_any=None, deadline: float | None = None):
    """캐시가 신선하면 재사용, 아니면 실제 수집. 실패하면 직전 값 유지(stale).

    deadline 을 넘기면 더 이상 수집하지 않고 직전 값을 그대로 쓴다 — 뉴스는
    부가 정보이므로 전체 실행을 붙잡을 가치가 없다.
    """
    if prev and not force and is_fresh(prev.get("fetched_at"), reuse) and prev.get("articles"):
        return prev
    if deadline is not None and time.monotonic() >= deadline:
        log(f"[news] 시간 예산 소진 — '{label}' 수집 건너뜀(직전 값 유지)")
        errors.append(f"뉴스 수집 시간 예산 소진({label}) — 직전 값 유지")
        if prev:
            carried = dict(prev)
            carried["stale"] = True
            return carried
        return None
    try:
        if state.get("fetched"):
            _pace_sleep(2.5, deadline)
        state["fetched"] = state.get("fetched", 0) + 1
        log(f"[news] 수집: {label}")
        arts = fetch_news_for(query, max_age, limit, require_tokens, context_any=context_any)
        return {"query": query, "articles": arts, "fetched_at": iso_kst(now_kst())}
    except Exception as e:
        log(f"[warn] 뉴스 수집 실패({label}): {e}")
        errors.append(f"뉴스 수집 실패({label}): {e}")
        if prev:
            carried = dict(prev)
            carried["stale"] = True
            return carried
        return None


def collect_news(cfg: dict, previous: dict, stock_names: dict | None = None,
                 force: bool = False) -> dict:
    stock_names = stock_names or {}
    prev_kw = (previous.get("keywords") or {})
    prev_sectors = {s.get("tag"): s for s in (previous.get("sectors") or [])}
    prev_companies = previous.get("companies") or {}
    prev_pool = previous.get("pool") or {}
    max_age = int(cfg.get("news_max_age_days", 10))
    limit = int(cfg.get("news_per_keyword", 30))
    state = {"fetched": 0}
    errors = []
    # 뉴스는 부가 정보 — 정해진 시간 안에서만 수집하고, 넘기면 캐시를 그대로 쓴다.
    deadline = time.monotonic() + max(1.0, min(NEWS_BUDGET_SECONDS, budget_left()))

    # 1) 기본 키워드 트레이스 / 2) 기본 맥락
    keywords = {}
    for kw in cfg["trace_keywords"]:
        label = kw["keyword"]
        query = kw.get("query") or label
        rule = keyword_rule(label, kw)
        entry = _refresh_entry(prev_kw.get(label), query,
                               int(cfg.get("news_reuse_minutes", 20)),
                               max_age, limit, errors, label, force, state,
                               rule["tokens"], context_any=rule["context_any"],
                               deadline=deadline)
        if entry:
            entry = dict(entry)
            entry.update({"keyword": label, "id": kw.get("id")})
            keywords[label] = entry

    # 2) 섹터 뉴스(브리핑 카드)
    sectors = []
    for sec in cfg["sector_news"]:
        tag = sec["tag"]
        query = sec.get("query") or tag
        entry = _refresh_entry(prev_sectors.get(tag), query,
                               int(cfg.get("sector_reuse_minutes", 60)),
                               max_age, 15, errors, tag, force, state,
                               deadline=deadline)
        if entry:
            entry = dict(entry)
            entry["tag"] = tag
            sectors.append(entry)

    # 3) 기업별 실적 뉴스 (오래된 순 회전 수집)
    company_queries = {}
    for code in list(cfg["kr_stocks"].keys()) + list(cfg["us_stocks"].keys()):
        name = stock_names.get(code) or cfg["kr_stocks"].get(code) or code
        company_queries[code] = f"{name} 실적"
    reuse_c = int(cfg.get("company_reuse_minutes", 60))
    candidates = []
    for code, query in company_queries.items():
        prev = prev_companies.get(code) or {}
        if force or not (prev and is_fresh(prev.get("fetched_at"), reuse_c) and prev.get("articles")):
            candidates.append((prev.get("fetched_at") or "", code))
    candidates.sort()
    selected = {code for _, code in candidates[:int(cfg.get("company_per_run", 2))]}

    companies = {}
    for code, query in company_queries.items():
        prev = prev_companies.get(code)
        if code in selected:
            entry = _refresh_entry(prev, query, 0, max_age, 8, errors, query, True, state,
                                   deadline=deadline)
        else:
            entry = prev
        if entry:
            entry = dict(entry)
            entry["code"] = code
            companies[code] = entry

    # 4) 사용자 추가 키워드 대비 주제 풀 (회전 수집)
    pool_queries = cfg.get("keyword_pool") or []
    reuse_p = int(cfg.get("pool_reuse_minutes", 120))
    pool_cands = []
    for kw in pool_queries:
        prev = prev_pool.get(kw) or {}
        if force or not (prev and is_fresh(prev.get("fetched_at"), reuse_p) and prev.get("articles")):
            pool_cands.append((prev.get("fetched_at") or "", kw))
    pool_cands.sort()
    selected_pool = {kw for _, kw in pool_cands[:int(cfg.get("pool_per_run", 3))]}
    pool = {}
    for kw in pool_queries:
        prev = prev_pool.get(kw)
        if kw in selected_pool:
            rule = keyword_rule(kw)
            entry = _refresh_entry(prev, kw, 0, max_age, 20, errors, kw, True, state,
                                   rule["tokens"], context_any=rule["context_any"],
                                   deadline=deadline)
        else:
            entry = prev
        if entry:
            entry = dict(entry)
            entry["keyword"] = kw
            pool[kw] = entry

    log(f"[news] 수집 {state['fetched']}회, 오류 {len(errors)}건 (소요 {elapsed_seconds():.0f}초)")
    return {"keywords": keywords, "sectors": sectors, "companies": companies,
            "pool": pool, "errors": errors}


def sanitize_news(news: dict, cfg: dict) -> dict:
    """저장·캐시된 기사까지 3중 필터로 다시 검사해 오탐을 걷어낸다.

    · keywords/pool 버킷: 토큰 경계·복합 맥락·노이즈 필터 적용 + 같은 제목 중복 제거
    · sectors/companies 버킷: 노이즈 필터만(기업명 표기가 달라도 버리지 않기 위해)
    반환: {"removed": 걷어낸 기사 수, "buckets": {버킷: 제거 수}}
    """
    news = news or {}
    removed = 0
    detail = {}

    def _filter_bucket(name: str, bucket, rule: dict, noise: bool = True) -> None:
        nonlocal removed
        if not isinstance(bucket, dict):
            return
        articles = list(bucket.get("articles") or [])
        kept, seen = [], set()
        for art in articles:
            if not article_passes(art, rule, cfg, noise=noise):
                continue
            key = title_key(str((art or {}).get("title") or ""))
            if key and key in seen:
                continue
            if key:
                seen.add(key)
            kept.append(art)
        drop = len(articles) - len(kept)
        if drop > 0:
            removed += drop
            detail[name] = drop
            bucket["articles"] = kept

    trace_rules = {kw["keyword"]: keyword_rule(kw["keyword"], kw)
                   for kw in (cfg.get("trace_keywords") or []) if kw.get("keyword")}
    for label, bucket in (news.get("keywords") or {}).items():
        _filter_bucket(f"keywords:{label}", bucket, trace_rules.get(label) or keyword_rule(label))
    for label, bucket in (news.get("pool") or {}).items():
        _filter_bucket(f"pool:{label}", bucket, keyword_rule(label))
    for bucket in (news.get("sectors") or []):
        _filter_bucket(f"sectors:{bucket.get('tag')}", bucket, {"tokens": [], "context_any": []})
    for code, bucket in (news.get("companies") or {}).items():
        _filter_bucket(f"companies:{code}", bucket, {"tokens": [], "context_any": []})
    if removed:
        log(f"[filter] 3중 필터로 오탐 {removed}건 제거 — {detail}")
    return {"removed": removed, "buckets": detail}


def sanitize_save_snapshot(snapshot: dict, cfg: dict) -> int:
    """SAVE 스냅샷에 붙어 있던 키워드 태그를 현재 규칙으로 다시 계산한다."""
    rules = save_keyword_rules(cfg)
    changed = 0
    for art in (snapshot or {}).get("news") or []:
        if not isinstance(art, dict):
            continue
        labels = [label for label, rule in rules if article_passes(art, rule, cfg)]
        if list(art.get("keywords") or []) != labels:
            art["keywords"] = labels
            if art.get("keyword") and art["keyword"] not in labels:
                art["keyword"] = labels[0] if labels else ""
            changed += 1
    return changed


def sanitize_company_news(payload: dict, cfg: dict) -> int:
    """기업별 뉴스(data/company_news.json)에 남은 비증시·사회 노이즈 기사를 걷어낸다."""
    removed = 0
    for entry in (payload or {}).get("companies", {}).values():
        if not isinstance(entry, dict):
            continue
        arts = list(entry.get("articles") or [])
        kept = [a for a in arts if not is_noise_title(str((a or {}).get("title") or ""), cfg)]
        if len(kept) != len(arts):
            removed += len(arts) - len(kept)
            entry["articles"] = kept
    return removed


def count_articles(news: dict) -> int:
    total = sum(len((b or {}).get("articles") or [])
                for b in (news.get("keywords") or {}).values())
    total += sum(len((b or {}).get("articles") or [])
                 for b in (news.get("pool") or {}).values())
    total += sum(len((b or {}).get("articles") or [])
                 for b in (news.get("companies") or {}).values())
    total += sum(len((b or {}).get("articles") or []) for b in (news.get("sectors") or []))
    return total


def refilter_data_files(cfg: dict) -> int:
    """수집 없이 저장된 data/news_latest.json·data/save_latest.json 만 재검사한다.

    이미 화면에 들어가 있던 오탐 기사(예: '수학이란, 신간', '반정부시위 사형선고')를
    다음 수집을 기다리지 않고 바로 걷어내는 용도.
    """
    if not os.path.exists(NEWS_PATH):
        log("[refilter] data/news_latest.json 이 없습니다")
        return 1
    news = load_json(NEWS_PATH, {})
    if not news:
        log("[refilter] news_latest.json 을 읽지 못했습니다")
        return 1
    before = count_articles(news)
    report = sanitize_news(news, cfg)
    after = count_articles(news)
    news.setdefault("filter", {})
    news["filter"].update({
        "rule": "3중 필터(단어 경계·복합 맥락·비증시 노이즈)",
        "refiltered_at": iso_kst(now_kst()),
        "removed_last_run": report["removed"],
        "buckets": report["buckets"],
    })
    with open(NEWS_PATH, "w", encoding="utf-8") as f:
        json.dump(news, f, ensure_ascii=False, indent=1)
        f.write("\n")
    save_changed = 0
    if os.path.exists(SAVE_PATH):
        snapshot = load_json(SAVE_PATH, {})
        if snapshot:
            save_changed = sanitize_save_snapshot(snapshot, cfg)
            if save_changed:
                with open(SAVE_PATH, "w", encoding="utf-8") as f:
                    json.dump(snapshot, f, ensure_ascii=False, indent=1)
                    f.write("\n")
    company_noise = 0
    if os.path.exists(COMPANY_NEWS_PATH):
        company_payload = load_json(COMPANY_NEWS_PATH, {})
        if company_payload:
            company_noise = sanitize_company_news(company_payload, cfg)
            if company_noise:
                with open(COMPANY_NEWS_PATH, "w", encoding="utf-8") as f:
                    json.dump(company_payload, f, ensure_ascii=False, separators=(",", ":"))
                    f.write("\n")
    print(json.dumps({"articles_before": before, "articles_after": after,
                      "removed": before - after, "buckets": report["buckets"],
                      "save_keyword_fix": save_changed,
                      "company_noise_removed": company_noise}, ensure_ascii=False, indent=1))
    return 0


def significant_change(old: dict, new: dict) -> bool:
    """커밋 여부 판단: 생성시각/에러를 제외한 실제 데이터가 달라졌는가."""
    def strip(payload):
        payload = json.loads(json.dumps(payload))
        payload.pop("generated_at", None)
        payload.pop("generated_at_kst", None)
        payload.pop("errors", None)
        for q in (payload.get("stocks") or {}).values():
            q.pop("as_of", None)
            q.pop("stale", None)
        for q in (payload.get("market", {}).get("indices") or {}).values():
            q.pop("as_of", None)
        for q in (payload.get("market", {}).get("policy_rates") or {}).values():
            # 조회 시각·상태는 매 실행마다 바뀐다 — 실제 값(as_of·금리)만 비교한다
            for k in ("checked_at", "stale", "status", "error"):
                q.pop(k, None)
        return payload
    return strip(old) != strip(new)


def _carry_market(prev_payload: dict) -> dict:
    """수집이 통째로 실패해도 앱이 빈 화면이 되지 않도록 직전 파일 값으로 채운다."""
    prev_payload = prev_payload or {}
    prev_market = prev_payload.get("market") or {}
    return {
        "indices": prev_market.get("indices") or {},
        "rates": prev_market.get("rates") or {},
        "fx": prev_market.get("fx") or {},
        "stocks": prev_payload.get("stocks") or prev_market.get("stocks") or {},
        "session": prev_market.get("session") or {},
        "policy_rates": prev_market.get("policy_rates") or {},
        "errors": ["시세 수집 실패 — 직전 값 유지"],
    }


def _carry_news(prev_payload: dict) -> dict:
    """뉴스 수집이 통째로 실패했을 때 직전 파일 값으로 채운다."""
    prev_payload = prev_payload or {}
    return {
        "keywords": prev_payload.get("keywords") or {},
        "sectors": prev_payload.get("sectors") or [],
        "companies": prev_payload.get("companies") or {},
        "pool": prev_payload.get("pool") or {},
        "errors": ["뉴스 수집 실패 — 직전 값 유지"],
    }


def _timed(label: str, fn, fallback=None):
    """구간을 돌리고 소요 시간을 로그로 남긴다. 실패해도 실행 전체가 멈추지 않는다."""
    started = time.monotonic()
    try:
        return fn()
    except Exception as e:  # noqa: BLE001 - 한 구간의 실패가 전체를 막지 않는다
        log(f"[warn] {label} 실패: {type(e).__name__}: {e}")
        return fallback() if callable(fallback) else fallback
    finally:
        log(f"[{label}] {time.monotonic() - started:.1f}초")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true", help="파일 기록 없이 결과만 확인")
    parser.add_argument("--force-news", action="store_true", help="뉴스 캐시 무시하고 재수집")
    parser.add_argument("--skip-universe", action="store_true", help="전종목 인덱스 수집 생략(빠른 확인용)")
    parser.add_argument("--refilter-data", action="store_true",
                        help="수집 없이 저장된 data/*.json 기사만 3중 필터로 재검사")
    args = parser.parse_args(argv)

    # 출력을 라인 단위로 즉시 내보낸다 — Actions 로그에서 멈춘 지점을 바로 본다.
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(line_buffering=True)
        except Exception:  # noqa: BLE001
            pass
    install_watchdog()
    log(f"[start] 라이브 데이터 수집 시작 (예산 {RUN_BUDGET_SECONDS:.0f}초)")

    cfg = load_config()
    if args.refilter_data:
        return refilter_data_files(cfg)
    prev_market = load_json(MARKET_PATH, {})
    prev_news = load_json(NEWS_PATH, {})
    prev_universe = load_json(UNIVERSE_PATH, {})
    fund_state = load_json(FUND_PATH, {})
    us_fund_state = dict(fund_state.get("us") or {})

    market = _timed("market", lambda: collect_market(cfg, prev_market),
                    lambda: _carry_market(prev_market))
    stock_names = {code: (q or {}).get("name") for code, q in market["stocks"].items()}
    news = _timed("news", lambda: collect_news(cfg, prev_news, stock_names,
                                               force=args.force_news),
                  lambda: _carry_news(prev_news))

    # SAVE(세이브티커) 연동 — 직접 조회가 막히면 직전 스냅샷을 그대로 쓴다(예외 없음)
    save_snapshot = _timed("save", lambda: collect_save(cfg, load_json(SAVE_PATH, {})),
                           lambda: {"status": "unavailable", "news": [],
                                    "top_stories": [], "calendar": [], "count": 0})
    news = merge_save_news(news, save_snapshot, cfg)

    # 3중 필터: 이번 수집·캐시 유지·SAVE 병합분까지 한 번 더 검사해 오탐을 걷어낸다
    filter_report = sanitize_news(news, cfg)

    universe = None
    company_news = None
    if args.skip_universe:
        pass
    elif budget_left() <= 5:
        log("[universe] 실행 예산 소진 — 전종목 인덱스 수집 건너뜀(직전 파일 유지)")
    else:
        try:
            universe, fund_state, us_fund_state = _timed(
                "universe", lambda: collect_universe(fund_state, us_fund_state, prev_universe))
        except Exception as e:  # noqa: BLE001
            log(f"[warn] 전종목 인덱스 수집 실패: {type(e).__name__}: {e}")
            universe = None
        if universe and universe.get("kr") and budget_left() > 8:
            try:
                company_news = _timed("company-news", lambda: collect_company_news(
                    universe, load_json(COMPANY_NEWS_PATH, {})))
                if company_news.get("errors"):
                    log("[warn] " + "; ".join(company_news["errors"][:3]))
            except Exception as e:  # noqa: BLE001
                log(f"[warn] 기업 뉴스 수집 실패: {e}")
                company_news = None

    generated = now_kst()
    market_payload = {
        "schema": 1,
        "generated_at": iso_kst(generated),
        "generated_at_kst": generated.strftime("%Y-%m-%d(%a) %H:%M").replace(
            "Mon", "월").replace("Tue", "화").replace("Wed", "수").replace(
            "Thu", "목").replace("Fri", "금").replace("Sat", "토").replace("Sun", "일"),
        "source": "네이버 금융 실시간 (polling.finance.naver.com)",
        "market": market,
        "stocks": market["stocks"],
        "briefing": build_briefing(
            market,
            {"kr": list(cfg["kr_stocks"].keys()), "us": list(cfg["us_stocks"].keys())},
            news,
        ),
    }
    # 파일 크기 절감: 화면에 필요한 만큼만 남긴다
    for entry in news["keywords"].values():
        entry["articles"] = entry.get("articles", [])[:30]
    for entry in news["pool"].values():
        entry["articles"] = entry.get("articles", [])[:10]
    for entry in news["companies"].values():
        entry["articles"] = entry.get("articles", [])[:6]
    for entry in news["sectors"]:
        entry["articles"] = entry.get("articles", [])[:8]

    news_payload = {
        "schema": 1,
        "generated_at": iso_kst(generated),
        "source": "Google News RSS (news.google.com) + SAVE(세이브티커)",
        "match": [kw["keyword"] for kw in cfg["trace_keywords"]],
        "keywords": news["keywords"],
        "sectors": news["sectors"],
        "companies": news["companies"],
        "pool": news["pool"],
        "save": news.get("save"),
        "filter": {"rule": "3중 필터(단어 경계·복합 맥락·비증시 노이즈)",
                   "removed": filter_report["removed"],
                   "buckets": filter_report["buckets"]},
        "errors": news["errors"],
    }
    news_payload["briefing"] = market_payload["briefing"]

    counts = {
        "stocks": len(market_payload["stocks"]),
        "indices": len(market_payload["market"]["indices"]),
        "keywords": len(news_payload["keywords"]),
        "articles": sum(len(v.get("articles") or []) for v in news_payload["keywords"].values()),
        "pool_topics": len(news_payload["pool"]),
        "company_news": len(news_payload["companies"]),
        "save_status": news_payload.get("save", {}).get("status"),
        "save_articles": news_payload.get("save", {}).get("count", 0),
        "filtered_out": filter_report["removed"],
        "errors": len(market.get("errors", [])) + len(news.get("errors", [])),
    }
    if universe:
        counts["universe_kr"] = universe["counts"]["kr"]
        counts["universe_us"] = universe["counts"]["us"]
        counts["universe_fund"] = universe["counts"]["fund"]
        counts["universe_us_fund"] = universe["counts"].get("us_fund", 0)
        counts["errors"] += len(universe.get("errors") or [])
    if company_news:
        counts["company_news_universe"] = len(company_news.get("companies") or {})
        counts["errors"] += len(company_news.get("errors") or [])

    if args.check:
        print(json.dumps({"counts": counts,
                          "headline": market_payload["briefing"]["headline"],
                          "generated_at": market_payload["generated_at"],
                          "errors": market.get("errors", []) + news.get("errors", [])},
                         ensure_ascii=False, indent=2))
        return 0

    os.makedirs(DATA_DIR, exist_ok=True)
    changed = True
    if prev_market:
        changed = significant_change(prev_market, market_payload)

    with open(MARKET_PATH, "w", encoding="utf-8") as f:
        json.dump(market_payload, f, ensure_ascii=False, indent=1)
        f.write("\n")
    with open(NEWS_PATH, "w", encoding="utf-8") as f:
        json.dump(news_payload, f, ensure_ascii=False, indent=1)
        f.write("\n")
    if universe:
        with open(UNIVERSE_PATH, "w", encoding="utf-8") as f:
            json.dump(universe, f, ensure_ascii=False, separators=(",", ":"))
            f.write("\n")
        with open(FUND_PATH, "w", encoding="utf-8") as f:
            json.dump({"date": fund_state.get("date"),
                       "complete": fund_state.get("complete"),
                       "items": fund_state.get("items") or {},
                       "us": {"date": us_fund_state.get("date"),
                              "complete": us_fund_state.get("complete"),
                              "items": us_fund_state.get("items") or {}}},
                      f, ensure_ascii=False, separators=(",", ":"))
            f.write("\n")
    # SAVE 스냅샷은 실제로 새로 받아온 경우에만 덮어쓴다(캐시 유지 = 마지막 성공 데이터 보존)
    if save_snapshot.get("status") == "live":
        with open(SAVE_PATH, "w", encoding="utf-8") as f:
            json.dump(save_snapshot, f, ensure_ascii=False, indent=1)
            f.write("\n")
    if company_news:
        company_payload = {"schema": 1,
                           "generated_at": iso_kst(generated),
                           "source": "Google News RSS (news.google.com)",
                           "companies": company_news.get("companies") or {}}
        counts["company_noise_removed"] = sanitize_company_news(company_payload, cfg)
        with open(COMPANY_NEWS_PATH, "w", encoding="utf-8") as f:
            json.dump(company_payload, f, ensure_ascii=False, separators=(",", ":"))
            f.write("\n")

    # 매수·매도 시점 분석용 일봉·수급·밸류에이션 수집(회전) — 부가 정보이므로 실패해도
    # 시세·뉴스 갱신을 막지 않는다. 워크플로가 커밋할 수 있도록 CI 에서만 파일을 stage 한다.
    if not args.check:
        try:
            import sys as _sys
            _sys.modules.setdefault("collect_live_data", _sys.modules[__name__])
            import collect_signals
            sig_budget = min(float(os.environ.get("COLLECT_SIGNAL_BUDGET_SECONDS", "120")),
                             max(10.0, budget_left() - 30))
            sig_result = collect_signals.collect(budget=sig_budget, workers=2)
            counts["signal_ok"] = sig_result.get("ok", 0)
            counts["signal_failed"] = sig_result.get("failed", 0)
        except Exception as e:  # noqa: BLE001
            log(f"[warn] 신호 데이터 수집 실패: {e}")
            counts["signal_error"] = str(e)[:120]

    log(f"[done] 생성 {market_payload['generated_at']} · 변경감지={changed} · 소요 {elapsed_seconds():.0f}초")
    print(json.dumps({"counts": counts, "changed": changed,
                      "headline": market_payload["briefing"]["headline"]},
                     ensure_ascii=False), flush=True)
    # 워크플로가 커밋 여부를 판단할 수 있도록 출력 파일로도 남긴다
    meta_path = os.path.join(DATA_DIR, ".last_run.json")
    with open(meta_path, "w", encoding="utf-8") as f:
        json.dump({"changed": changed, "counts": counts,
                   "generated_at": market_payload["generated_at"]}, f, ensure_ascii=False)
    return 0


if __name__ == "__main__":
    sys.exit(main())
