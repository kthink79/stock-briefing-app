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
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime

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
        {"id": "trace_iran", "keyword": "미국 이란 전쟁", "query": "이란", "require": ["이란"]},
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


def http_get(url: str, timeout: int = 15, retries: int = 2) -> bytes:
    last_err = None
    for attempt in range(retries + 1):
        req = urllib.request.Request(url, headers={
            "User-Agent": UA,
            "Accept": "*/*",
            "Accept-Language": "ko-KR,ko;q=0.9,en;q=0.8",
        })
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.read()
        except urllib.error.HTTPError as e:  # 429/500 등
            last_err = f"HTTP {e.code} {url}"
            if e.code in (429, 500, 502, 503, 504):
                time.sleep(1.5 * (attempt + 1))
                continue
            raise
        except Exception as e:  # urllib.error.URLError ...
            last_err = f"{type(e).__name__}: {e} ({url})"
            time.sleep(1.0 * (attempt + 1))
    raise RuntimeError(last_err or f"request failed: {url}")


def get_json(url: str) -> dict:
    return json.loads(http_get(url).decode("utf-8", "replace"))


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
    except Exception:
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
        csv_text = http_get(FRED_TARGET_CSV, timeout=10, retries=1).decode("utf-8", "replace")
        parsed = parse_fred_target(csv_text)
        if parsed:
            return parsed
        errors.append("FRED 응답에 목표범위 없음")
    except Exception as e:
        errors.append(f"FRED 조회 실패: {e}")
    raise RuntimeError("; ".join(errors))


def fetch_kr_policy_rate() -> dict:
    """한국은행 기준금리: 기준금리 추이 표 파싱."""
    html = http_get(BOK_BASE_RATE, timeout=20, retries=1).decode("utf-8", "replace")
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


def keyword_tokens(keyword: str) -> list:
    """'미국 이란 전쟁' → ['미국','이란','전쟁'] 같은 관련성 판정용 토큰."""
    return [t for t in re.split(r"\s+", keyword or "") if len(t) >= 2]


def filter_relevant(articles: list, tokens: list) -> list:
    """제목에 토큰이 하나도 없는 기사는 검색 노이즈로 보고 제외한다."""
    if not tokens:
        return articles
    lowered = [t.lower() for t in tokens]
    out = []
    for a in articles:
        title = (a.get("title") or "").lower()
        if any(t in title for t in lowered):
            out.append(a)
    return out


def fetch_news_for(query: str, max_age_days: int = 10, limit: int = 30,
                   require_tokens: list | None = None) -> list:
    params = urllib.parse.urlencode({
        "q": query, "hl": "ko", "gl": "KR", "ceid": "KR:ko",
    })
    xml_bytes = http_get(f"{GOOGLE_NEWS}?{params}", timeout=25, retries=3)
    articles = parse_google_news_rss(xml_bytes, query, max_age_days * 2, limit * 3)
    if require_tokens:
        articles = filter_relevant(articles, require_tokens)
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


def fetch_kr_universe(page_size: int = 100, max_pages: int = 60) -> dict:
    """코스피·코스닥 전 종목의 이름·코드·현재가·등락률·시가총액을 수집한다."""
    out = {}
    for market in ("KOSPI", "KOSDAQ"):
        page = 1
        while page <= max_pages:
            url = f"{NAVER_LIST}/{market}?page={page}&pageSize={page_size}"
            payload = get_json(url)
            rows = payload.get("stocks") or []
            if not rows:
                break
            for item in rows:
                entry = parse_market_list_item(item)
                if entry:
                    out[entry["code"]] = entry
            total = num(payload.get("totalCount")) or 0
            if page * page_size >= total:
                break
            page += 1
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
        url = f"{NAVER_POLL}/worldstock/stock/" + ",".join(codes[i:i + chunk])
        payload = get_json(url)
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


def fetch_daum_fundamental(code: str, timeout: int = 12) -> dict | None:
    req = urllib.request.Request(
        f"{DAUM_QUOTE}{code}?summary=false",
        headers={"User-Agent": UA, "Referer": "https://finance.daum.net/"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        data = json.loads(resp.read().decode("utf-8", "replace"))
    return parse_daum_fundamental(data)


def refresh_fundamentals(codes, previous: dict, today: str,
                         workers: int = 8, budget: int | None = None) -> dict:
    """시가총액 상위 국내 종목의 PER/PBR/EPS/BPS를 하루 1회 갱신한다.

    previous: 이전 stock_fundamentals.json ({"date": ..., "items": {code: {...}}})
    """
    from concurrent.futures import ThreadPoolExecutor

    prev_items = dict(previous.get("items") or {})
    targets = [c for c in codes if (prev_items.get(c) or {}).get("d") != today]
    if budget is not None:
        targets = targets[:budget]
    if not targets:
        return {"date": today, "items": prev_items}

    def one(code):
        for attempt in range(2):
            try:
                f = fetch_daum_fundamental(code)
                if f is None:
                    return code, None
                f["d"] = today
                return code, f
            except Exception:
                time.sleep(0.4 * (attempt + 1))
        return code, None

    done = 0
    with ThreadPoolExecutor(max_workers=workers) as ex:
        for code, f in ex.map(one, targets):
            done += 1
            if f:
                prev_items[code] = f
    print(f"[universe] 재무지표 갱신 {done}건 (시도 {len(targets)})", file=sys.stderr)
    return {"date": today, "items": prev_items}


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
    for code in selected:
        name = targets.get(code) or code
        try:
            arts = fetch_news_for(name, max_age_days=10, limit=8,
                                  require_tokens=keyword_tokens(name))
        except Exception as e:
            errors.append(f"{name} 뉴스 수집 실패: {e}")
            continue
        time.sleep(0.4)
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


def fetch_us_fundamental(reuters_code: str, timeout: int = 12) -> dict | None:
    base = f"https://api.stock.naver.com/stock/{reuters_code}"
    try:
        summary = get_json(f"{base}/finance/summary")
    except Exception:
        summary = {}
    try:
        quarter = get_json(f"{base}/finance/quarter")
    except Exception:
        quarter = {}
    return parse_us_fundamentals(summary, quarter)


def refresh_us_fundamentals(us: dict, previous: dict, today: str,
                            workers: int = 6, limit: int = US_FUND_LIMIT) -> dict:
    """시가총액 상위 미국 종목의 TTM EPS·PBR을 하루 1회 갚신한다."""
    from concurrent.futures import ThreadPoolExecutor

    prev_items = dict(previous.get("items") or {})
    ranked = sorted(us.items(), key=lambda kv: (-(kv[1].get("mcap") or 0), kv[0]))[:limit]
    targets = [(sym, e.get("reuters") or sym) for sym, e in ranked]
    if not targets:
        return {"date": today, "items": prev_items}

    def one(item):
        sym, reuters = item
        try:
            f = fetch_us_fundamental(reuters)
            if f:
                f["d"] = today
            return sym, f
        except Exception:
            return sym, None

    done = 0
    with ThreadPoolExecutor(max_workers=workers) as ex:
        for sym, f in ex.map(one, targets):
            done += 1
            if f:
                prev_items[sym] = f
    print(f"[universe] 미국 재무지표 갱신 {done}건", file=sys.stderr)
    return {"date": today, "items": prev_items}


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
    if kr and now_kst().hour >= UNIVERSE_FUND_MIN_HOUR and (fund_state.get("date") != today):
        # 시가총액 상위 종목만 재무지표 대상으로 삼는다
        ranked = sorted(kr.items(), key=lambda kv: (-(kv[1].get("mcap_eok") or 0), kv[0]))
        codes = [c for c, e in ranked[:UNIVERSE_FUND_LIMIT]]
        try:
            fund_state = refresh_fundamentals(codes, fund_state, today)
        except Exception as e:
            errors.append(f"재무지표 갱신 실패: {e}")

    if us and now_kst().hour >= UNIVERSE_FUND_MIN_HOUR and (us_fund_state.get("date") != today):
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

    try:
        market["stocks"].update(fetch_world_stocks(cfg["us_stocks"]))
    except Exception as e:
        errors.append(f"해외 주가 수집 실패: {e}")

    try:
        market["indices"].update(fetch_domestic_index(cfg["kr_indices"]))
    except Exception as e:
        errors.append(f"국내 지수 수집 실패: {e}")

    try:
        market["indices"].update(fetch_world_index(cfg["us_indices"]))
    except Exception as e:
        errors.append(f"해외 지수 수집 실패: {e}")

    market["rates"] = fetch_rates(cfg["rates"])
    if not market["rates"]:
        errors.append("금리 수집 실패")
        market["rates"] = previous.get("market", {}).get("rates") or {}

    market["fx"] = fetch_fx(cfg["fx"])
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


def _paced_fetch(state: dict, query: str, max_age: int, limit: int) -> list:
    if state["fetched"]:
        time.sleep(2.5)
    state["fetched"] += 1
    return fetch_news_for(query, max_age, limit)


def _refresh_entry(prev, query, reuse, max_age, limit, errors, label, force, state,
                   require_tokens=None):
    """캐시가 신선하면 재사용, 아니면 실제 수집. 실패하면 직전 값 유지(stale)."""
    if prev and not force and is_fresh(prev.get("fetched_at"), reuse) and prev.get("articles"):
        return prev
    try:
        if state.get("fetched"):
            time.sleep(2.5)
        state["fetched"] = state.get("fetched", 0) + 1
        arts = fetch_news_for(query, max_age, limit, require_tokens)
        return {"query": query, "articles": arts, "fetched_at": iso_kst(now_kst())}
    except Exception as e:
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

    # 1) 기본 키워드 트레이스
    keywords = {}
    for kw in cfg["trace_keywords"]:
        label = kw["keyword"]
        query = kw.get("query") or label
        entry = _refresh_entry(prev_kw.get(label), query,
                               int(cfg.get("news_reuse_minutes", 20)),
                               max_age, limit, errors, label, force, state,
                               kw.get("require") or keyword_tokens(label))
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
                               max_age, 15, errors, tag, force, state)
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
            entry = _refresh_entry(prev, query, 0, max_age, 8, errors, query, True, state)
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
            entry = _refresh_entry(prev, kw, 0, max_age, 20, errors, kw, True, state,
                                   keyword_tokens(kw))
        else:
            entry = prev
        if entry:
            entry = dict(entry)
            entry["keyword"] = kw
            pool[kw] = entry

    return {"keywords": keywords, "sectors": sectors, "companies": companies,
            "pool": pool, "errors": errors}


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


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true", help="파일 기록 없이 결과만 확인")
    parser.add_argument("--force-news", action="store_true", help="뉴스 캐시 무시하고 재수집")
    parser.add_argument("--skip-universe", action="store_true", help="전종목 인덱스 수집 생략(빠른 확인용)")
    args = parser.parse_args(argv)

    cfg = load_config()
    prev_market = load_json(MARKET_PATH, {})
    prev_news = load_json(NEWS_PATH, {})
    prev_universe = load_json(UNIVERSE_PATH, {})
    fund_state = load_json(FUND_PATH, {})
    us_fund_state = dict(fund_state.get("us") or {})

    market = collect_market(cfg, prev_market)
    stock_names = {code: (q or {}).get("name") for code, q in market["stocks"].items()}
    news = collect_news(cfg, prev_news, stock_names, force=args.force_news)

    universe = None
    company_news = None
    if not args.skip_universe:
        universe, fund_state, us_fund_state = collect_universe(fund_state, us_fund_state, prev_universe)
        if universe and universe.get("kr"):
            try:
                company_news = collect_company_news(
                    universe, load_json(COMPANY_NEWS_PATH, {}))
                if company_news.get("errors"):
                    print("[warn] " + "; ".join(company_news["errors"][:3]), file=sys.stderr)
            except Exception as e:
                print(f"[warn] 기업 뉴스 수집 실패: {e}", file=sys.stderr)

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
        "source": "Google News RSS (news.google.com)",
        "match": [kw["keyword"] for kw in cfg["trace_keywords"]],
        "keywords": news["keywords"],
        "sectors": news["sectors"],
        "companies": news["companies"],
        "pool": news["pool"],
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
                       "items": fund_state.get("items") or {},
                       "us": {"date": us_fund_state.get("date"),
                              "items": us_fund_state.get("items") or {}}},
                      f, ensure_ascii=False, separators=(",", ":"))
            f.write("\n")
    if company_news:
        with open(COMPANY_NEWS_PATH, "w", encoding="utf-8") as f:
            json.dump({"schema": 1,
                       "generated_at": iso_kst(generated),
                       "source": "Google News RSS (news.google.com)",
                       "companies": company_news.get("companies") or {}},
                      f, ensure_ascii=False, separators=(",", ":"))
            f.write("\n")

    print(json.dumps({"counts": counts, "changed": changed,
                      "headline": market_payload["briefing"]["headline"]},
                     ensure_ascii=False))
    # 워크플로가 커밋 여부를 판단할 수 있도록 출력 파일로도 남긴다
    meta_path = os.path.join(DATA_DIR, ".last_run.json")
    with open(meta_path, "w", encoding="utf-8") as f:
        json.dump({"changed": changed, "counts": counts,
                   "generated_at": market_payload["generated_at"]}, f, ensure_ascii=False)
    return 0


if __name__ == "__main__":
    sys.exit(main())
