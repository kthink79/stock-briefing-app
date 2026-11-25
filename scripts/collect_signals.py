#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""매수·매도 시점 분석용 데이터 수집기 (일봉 OHLCV · 수급 · 밸류에이션).

앱의 신호 엔진(`signal-engine.js`)이 브라우저에서 점수를 계산하는 데 쓰는 원천
데이터를 종목별 파일로 나누어 만든다. 점수 계산 자체는 하지 않는다 — 가중치
슬라이더를 움직이면 즉시 다시 계산되어야 하므로 규칙 계산은 클라이언트에 있다.

  data/signal/h/<종목코드>.json   종목 1개: 120일 일봉 + 최근 수급 + 밸류에이션
  data/signal/manifest.json       수집 대상 목록·갱신 시각(상태 표시용)

수집 대상
  · 국내: data/config.json 등록 종목 + 시가총액 상위 N종목
  · 미국: data/config.json 등록 종목 + 시가총액 상위 M종목
  · 120거래일 일봉: 네이버 금융 siseJson (국내) / Yahoo Finance chart (미국)
  · 수급·밸류에이션: 네이버 금융 모바일 integration JSON (국내)

한 실행에서 모든 종목을 받을 수는 없으므로(HISTORY_TTL 시간이 지난 종목부터)
회전 수집한다. 실패한 종목은 직전 파일을 그대로 두고 manifest 에 상태를 남긴다
(가짜 값 금지 — 앱의 다른 수집기와 같은 원칙).

사용:
  python3 scripts/collect_signals.py                 # 예산 안에서 회전 수집
  python3 scripts/collect_signals.py --limit 50      # 이번 실행에서 50종목만
  python3 scripts/collect_signals.py --force --limit 700 --workers 4   # 초기 적재
  python3 scripts/collect_signals.py --check         # 파일을 쓰지 않고 현황만 출력
"""
from __future__ import annotations

import argparse
import concurrent.futures
import json
import os
import re
import sys
import threading
import time
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import collect_live_data as live  # noqa: E402

ROOT = live.ROOT
DATA_DIR = live.DATA_DIR
SIGNAL_DIR = os.path.join(DATA_DIR, "signal")
HISTORY_DIR = os.path.join(SIGNAL_DIR, "h")
MANIFEST_PATH = os.path.join(SIGNAL_DIR, "manifest.json")
UNIVERSE_PATH = live.UNIVERSE_PATH
FUND_PATH = live.FUND_PATH

SCHEMA_VERSION = 1
BARS_LIMIT = 120                 # 120거래일 일봉
KR_COVER_LIMIT = int(os.environ.get("SIGNAL_KR_COVER", "420"))
US_COVER_LIMIT = int(os.environ.get("SIGNAL_US_COVER", "110"))
HISTORY_TTL_HOURS = float(os.environ.get("SIGNAL_HISTORY_TTL_HOURS", "20"))
FACTOR_TTL_HOURS = float(os.environ.get("SIGNAL_FACTOR_TTL_HOURS", "6"))
SIGNAL_BUDGET_SECONDS = float(os.environ.get("COLLECT_SIGNAL_BUDGET_SECONDS", "120"))
CALL_SECONDS = float(os.environ.get("COLLECT_SIGNAL_CALL_SECONDS", "12"))
DEFAULT_PER_RUN = int(os.environ.get("SIGNAL_PER_RUN", "90"))

NAVER_SISE = "https://api.finance.naver.com/siseJson.naver"
NAVER_INTEGRATION = "https://m.stock.naver.com/api/stock/{code}/integration"
YAHOO_CHART = "https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"

_naver_row = re.compile(
    r'\["(\d{8})"\s*,\s*([\d.]+)\s*,\s*([\d.]+)\s*,\s*([\d.]+)\s*,\s*([\d.]+)'
    r'\s*,\s*([\d.]+)\s*,\s*(-?[\d.]+)?\s*\]'
)
_num_in_text = re.compile(r'[-+]?\d[\d,]*(?:\.\d+)?')


def num_text(value):
    """'5.25배' / '31,809원' / '+2,348,072' 같은 표시 문자열에서 숫자만 뽑는다."""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    match = _num_in_text.search(str(value))
    if not match:
        return None
    try:
        return float(match.group(0).replace(",", ""))
    except ValueError:
        return None

_log_lock = threading.Lock()
_budget_state = {"deadline": None}


def log(message: str) -> None:
    with _log_lock:
        live.log(message)


def _deadline() -> float | None:
    return _budget_state["deadline"]


def remaining() -> float:
    deadline = _deadline()
    if deadline is None:
        return 10 ** 9
    return max(0.0, deadline - time.monotonic())


def _now() -> datetime:
    return live.now_kst()


def _iso(dt: datetime) -> str:
    return dt.isoformat(timespec="seconds")


def _parse_iso(text) -> datetime | None:
    if not text:
        return None
    try:
        return datetime.fromisoformat(str(text))
    except ValueError:
        return None


def _age_hours(text=None) -> float:
    dt = _parse_iso(text)
    if not dt:
        return 10 ** 6
    now = _now()
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=now.tzinfo)
    return (now - dt).total_seconds() / 3600.0


# --------------------------------------------------------------------------
# 수집 대상
# --------------------------------------------------------------------------
def _cap_of(row) -> float:
    """universe 행에서 시가총액(원/달러)을 꺼낸다. 없으면 0."""
    try:
        value = row[4]
    except (IndexError, TypeError):
        return 0.0
    return float(value) if isinstance(value, (int, float)) else 0.0


def build_coverage(config: dict, universe: dict, prev_manifest: dict | None = None,
                   kr_limit: int = KR_COVER_LIMIT, us_limit: int = US_COVER_LIMIT) -> list:
    """수집 대상 목록 — config 등록 종목이 항상 맨 앞(매 실행 우선 갱신)."""
    prev_codes = ((prev_manifest or {}).get("codes") or {})
    kr_rows = (universe or {}).get("kr") or {}
    us_rows = (universe or {}).get("us") or {}
    target = []

    def push(code, market, name, rank):
        target.append({"code": code, "market": market, "name": name or code, "rank": rank})

    seen = set()
    for code, name in (config.get("kr_stocks") or {}).items():
        if code not in seen:
            seen.add(code)
            push(code, "KR", (kr_rows.get(code) or [name])[0] or name, 0)
    for code, name in (config.get("us_stocks") or {}).items():
        if code not in seen:
            seen.add(code)
            push(code, "US", (us_rows.get(code) or [name])[0] or name, 0)

    kr_sorted = sorted(kr_rows.items(), key=lambda kv: _cap_of(kv[1]), reverse=True)
    for rank, (code, row) in enumerate(kr_sorted[:max(0, kr_limit)], start=1):
        if code in seen:
            continue
        seen.add(code)
        push(code, "KR", row[0] if row else code, rank)

    us_sorted = sorted(us_rows.items(), key=lambda kv: _cap_of(kv[1]), reverse=True)
    for rank, (code, row) in enumerate(us_sorted[:max(0, us_limit)], start=1):
        if code in seen:
            continue
        seen.add(code)
        push(code, "US", row[0] if row else code, rank)

    # 갱신이 오래된 종목을 우선한다(회전). 한 번도 받지 못한 종목은 맨 앞.
    def sort_key(item):
        meta = prev_codes.get(item["code"]) or {}
        stamp = meta.get("updated_at") or meta.get("u")
        age = _age_hours(stamp) if meta else 10 ** 6
        return (0 if item["rank"] == 0 else 1, -age, item["rank"])

    return sorted(target, key=sort_key)


# --------------------------------------------------------------------------
# 파싱
# --------------------------------------------------------------------------
def parse_naver_sise(text: str) -> list:
    """네이버 siseJson 응답 → [[yyyymmdd, 시가, 고가, 저가, 종가, 거래량, 외인소진율], ...]."""
    bars = []
    for m in _naver_row.finditer(text or ""):
        date, o, h, low, c, volume, frgn = m.groups()
        try:
            bar = [int(date), int(round(float(o))), int(round(float(h))),
                   int(round(float(low))), int(round(float(c))),
                   int(float(volume)), round(float(frgn), 2) if frgn else None]
        except ValueError:
            continue
        if bar[4] <= 0:
            continue
        bars.append(bar)
    bars.sort(key=lambda b: b[0])
    return bars[-BARS_LIMIT:]


def parse_yahoo_chart(payload: dict) -> tuple:
    """Yahoo chart JSON → (bars, meta). bars = [[yyyymmdd, o,h,l,c,v, None], ...]."""
    result = ((payload or {}).get("chart") or {}).get("result") or []
    if not result:
        return [], {}
    data = result[0]
    meta = data.get("meta") or {}
    stamps = data.get("timestamp") or []
    quote = ((data.get("indicators") or {}).get("quote") or [{}])[0]
    opens = quote.get("open") or []
    highs = quote.get("high") or []
    lows = quote.get("low") or []
    closes = quote.get("close") or []
    volumes = quote.get("volume") or []
    kst = live.KST
    bars = []
    for i, stamp in enumerate(stamps):
        try:
            close = closes[i]
        except IndexError:
            break
        if close is None:
            continue
        day = datetime.fromtimestamp(int(stamp), kst).strftime("%Y%m%d")
        try:
            bar = [int(day),
                   int(round(opens[i] if opens[i] is not None else close)),
                   int(round(highs[i] if highs[i] is not None else close)),
                   int(round(lows[i] if lows[i] is not None else close)),
                   int(round(close)),
                   int(volumes[i] or 0),
                   None]
        except (TypeError, ValueError):
            continue
        bars.append(bar)
    return bars[-BARS_LIMIT:], meta


def parse_integration(payload: dict) -> dict:
    """네이버 integration JSON → {supply: {...}, valuation: {...}} (실패 항목은 None)."""
    infos = {}
    for item in (payload or {}).get("totalInfos") or []:
        code = item.get("code")
        if code:
            infos[code] = item.get("value")
    valuation = {
        "per": num_text(infos.get("per")),
        "pbr": num_text(infos.get("pbr")),
        "eps": num_text(infos.get("eps")),
        "bps": num_text(infos.get("bps")),
        "high52": num_text(infos.get("highPriceOf52Weeks")),
        "low52": num_text(infos.get("lowPriceOf52Weeks")),
        "div": num_text(infos.get("dividendYieldRatio")),
        "foreign_rate": num_text(infos.get("foreignRate")),
        "cns_per": num_text(infos.get("cnsPer")),
    }
    days = []
    for row in (payload or {}).get("dealTrendInfos") or []:
        bizdate = str(row.get("bizdate") or "")
        if not bizdate:
            continue
        days.append({
            "d": bizdate,
            "f": num_text(row.get("foreignerPureBuyQuant")),
            "o": num_text(row.get("organPureBuyQuant")),
            "i": num_text(row.get("individualPureBuyQuant")),
            "rate": num_text(row.get("foreignerHoldRatio")),
        })
    days.sort(key=lambda d: d["d"], reverse=True)
    supply = {"asof": days[0]["d"] if days else None, "days": days[:10],
              "foreign_rate": valuation.get("foreign_rate")}
    if valuation.get("foreign_rate") is None and days:
        valuation["foreign_rate"] = days[0].get("rate")
    return {"supply": supply, "valuation": valuation}


# --------------------------------------------------------------------------
# 네트워크
# --------------------------------------------------------------------------
def fetch_naver_history(code: str, days: int = BARS_LIMIT) -> list:
    end = _now()
    start = end - timedelta(days=int(days * 1.9) + 20)
    url = (f"{NAVER_SISE}?symbol={code}&requestType=1"
           f"&startTime={start.strftime('%Y%m%d')}&endTime={end.strftime('%Y%m%d')}"
           f"&timeframe=day")
    body = live.http_get(url, max_seconds=min(CALL_SECONDS, max(1.0, remaining())))
    return parse_naver_sise(body.decode("utf-8", "replace"))


def fetch_yahoo_history(symbol: str) -> tuple:
    url = YAHOO_CHART.format(symbol=symbol) + "?range=1y&interval=1d"
    body = live.http_get(url, max_seconds=min(CALL_SECONDS, max(1.0, remaining())))
    try:
        payload = json.loads(body.decode("utf-8", "replace"))
    except ValueError:
        return [], {}
    return parse_yahoo_chart(payload)


def fetch_integration(code: str) -> dict:
    body = live.http_get(NAVER_INTEGRATION.format(code=code),
                         max_seconds=min(CALL_SECONDS, max(1.0, remaining())))
    try:
        payload = json.loads(body.decode("utf-8", "replace"))
    except ValueError:
        return {"supply": {"asof": None, "days": []}, "valuation": {}}
    return parse_integration(payload)


def _fundamentals_entry(code: str, market: str, funds: dict) -> dict:
    if market == "US":
        items = ((funds or {}).get("us") or {}).get("items") or {}
    else:
        items = (funds or {}).get("items") or {}
    entry = items.get(code) or {}
    return entry if isinstance(entry, dict) else {}


def build_record(code: str, market: str, name: str, funds: dict | None = None) -> dict:
    """한 종목의 원천 데이터를 모아 레코드를 만든다. 봉이 하나도 없으면 None.

    국내 종목은 integration 조회가 실패해도 일봉만으로 레코드를 만든다 — 그 경우
    수급·밸류에이션 규칙은 '데이터 없음'으로 표시되고 가중치가 자동 보정된다.
    """
    if market == "US":
        symbol = code if "." in code or not code.isdigit() else code
        bars, meta = fetch_yahoo_history(symbol)
        price = live.num(meta.get("regularMarketPrice"))
        valuation = {
            "per": None,
            "pbr": None,
            "eps": None,
            "bps": None,
            "high52": live.num(meta.get("fiftyTwoWeekHigh")),
            "low52": live.num(meta.get("fiftyTwoWeekLow")),
            "div": None,
            "foreign_rate": None,
        }
        fund = _fundamentals_entry(code, market, funds or {})
        valuation["pbr"] = fund.get("pbr")
        valuation["eps"] = fund.get("eps")
        if valuation["eps"] and price:
            valuation["per"] = round(price / valuation["eps"], 2)
        supply = {"asof": None, "days": [], "foreign_rate": None}
        currency = meta.get("currency") or "USD"
    else:
        bars = fetch_naver_history(code)
        price = bars[-1][4] if bars else None
        try:
            factors = fetch_integration(code)
        except Exception as e:  # noqa: BLE001 - 일봉만으로도 레코드는 만든다
            log(f"[warn] 수급/밸류에이션 실패 {code}: {e}")
            factors = {"supply": {"asof": None, "days": []}, "valuation": {}}
        supply = factors.get("supply") or {"asof": None, "days": []}
        valuation = factors.get("valuation") or {}
        fund = _fundamentals_entry(code, market, funds or {})
        for key in ("per", "pbr", "eps", "bps"):
            if valuation.get(key) is None and fund.get(key) is not None:
                valuation[key] = fund.get(key)
        currency = "KRW"
    if not bars:
        return None
    return {
        "schema": SCHEMA_VERSION,
        "code": code,
        "name": name or code,
        "market": market,
        "currency": currency,
        "updated_at": _iso(_now()),
        "price": price,
        "bars": bars,
        "supply": supply,
        "valuation": valuation,
    }


# --------------------------------------------------------------------------
# 저장
# --------------------------------------------------------------------------
def _ensure_dirs() -> None:
    os.makedirs(HISTORY_DIR, exist_ok=True)


def record_path(code: str) -> str:
    safe = re.sub(r"[^0-9A-Za-z._-]", "_", str(code))
    return os.path.join(HISTORY_DIR, f"{safe}.json")


def load_manifest() -> dict:
    try:
        with open(MANIFEST_PATH, encoding="utf-8") as f:
            return json.load(f) or {}
    except (OSError, ValueError):
        return {}


def load_record(code: str) -> dict | None:
    try:
        with open(record_path(code), encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def write_record(record: dict) -> str:
    path = record_path(record["code"])
    with open(path, "w", encoding="utf-8") as f:
        json.dump(record, f, ensure_ascii=False, separators=(",", ":"))
        f.write("\n")
    return path


def write_manifest(coverage: list, records: dict, wrote: list) -> str:
    codes = {}
    for item in coverage:
        code = item["code"]
        rec = records.get(code) or load_record(code) or {}
        meta = {
            "n": rec.get("name") or item.get("name") or code,
            "m": item["market"],
        }
        if rec.get("updated_at"):
            meta["u"] = rec["updated_at"]
        if rec.get("bars"):
            meta["b"] = len(rec["bars"])
            meta["last"] = str(rec["bars"][-1][0])
        codes[code] = meta
    payload = {
        "schema": SCHEMA_VERSION,
        "generated_at": _iso(_now()),
        "bars_limit": BARS_LIMIT,
        "counts": {
            "kr": sum(1 for v in codes.values() if v["m"] == "KR"),
            "us": sum(1 for v in codes.values() if v["m"] == "US"),
            "with_bars": sum(1 for v in codes.values() if v.get("b")),
        },
        "codes": codes,
    }
    with open(MANIFEST_PATH, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, separators=(",", ":"))
        f.write("\n")
    return MANIFEST_PATH


def stage_for_ci(paths: list) -> None:
    """GitHub Actions 에서만: 다음 워크플로 스텝이 커밋하도록 파일을 stage 한다.

    live-data 워크플로는 `git add` 목록이 고정돼 있어 새 파일(종목별 신호 데이터)이
    커밋되지 않는다. 워크플로 파일은 workflows 권한이 없어 고칠 수 없으므로,
    수집기 자신이 CI 환경에서만 stage 한다. 로컬 실행에는 아무 영향이 없다.
    """
    if os.environ.get("GITHUB_ACTIONS") != "true" or not paths:
        return
    import subprocess
    try:
        existing = [p for p in paths if os.path.exists(p)]
        if not existing:
            return
        subprocess.run(["git", "add", "--"] + existing, cwd=ROOT, check=False,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        log(f"[signal] CI 커밋 대상 stage: {len(existing)}개 파일")
    except Exception as e:  # noqa: BLE001
        log(f"[warn] stage 실패: {e}")


# --------------------------------------------------------------------------
# 실행
# --------------------------------------------------------------------------
def needs_refresh(prev: dict | None, now: datetime, history_ttl: float, factor_ttl: float) -> bool:
    """다시 받을지 판단한다.

    · 오늘 봉이 없고 마지막 봉이 30시간 이상 지났으면 받는다(장 마감 뒤 새 봉 반영).
      단, 직전 수집이 6시간 안이면 건너뛴다 — 주말·휴장일에 10분마다 전 종목을
      반복 수집하며 커밋이 쌓이는 것을 막는다.
    · 받은 지 20시간이 지났으면 받는다(하루 1회 갱신).
    """
    if not prev or not prev.get("bars"):
        return True
    age = _age_hours(prev.get("updated_at"))
    if age >= history_ttl:
        return True
    if age < min(factor_ttl, history_ttl):
        return False
    last = str(prev["bars"][-1][0])
    today = now.strftime("%Y%m%d")
    if last == today:
        return False
    # 주말·휴장을 감안해 30시간 이상 지난 봉이면 다시 받는다
    try:
        last_dt = datetime.strptime(last, "%Y%m%d").replace(tzinfo=now.tzinfo)
    except ValueError:
        return True
    return (now - last_dt).total_seconds() / 3600.0 >= 30


def collect(limit: int | None = None, budget: float | None = None, force: bool = False,
            workers: int = 1, dry: bool = False, coverage: list | None = None,
            fetch=build_record, keep_good: bool = True) -> dict:
    """예산 안에서 신선도가 떨어진 종목을 회전 수집한다."""
    start = time.monotonic()
    _budget_state["deadline"] = start + float(budget if budget is not None else SIGNAL_BUDGET_SECONDS)
    _ensure_dirs()
    config = live.load_config()
    universe = live.load_json(UNIVERSE_PATH, {}) or {}
    funds = live.load_json(FUND_PATH, {}) or {}
    manifest = load_manifest()
    coverage = coverage if coverage is not None else build_coverage(config, universe, manifest)
    limit = DEFAULT_PER_RUN if limit is None else int(limit)

    records = {}
    targets = []
    for item in coverage:
        code = item["code"]
        prev = load_record(code)
        records[code] = prev or {}
        if force or needs_refresh(prev, _now(), HISTORY_TTL_HOURS, FACTOR_TTL_HOURS):
            targets.append(item)
    planned = targets[:max(0, limit)] if limit > 0 else []

    log(f"[signal] 대상 {len(coverage)}종목 · 갱신 필요 {len(targets)} · 이번 실행 {len(planned)}"
        f" · 예산 {remaining():.0f}초")

    results = {"ok": 0, "failed": 0, "skipped": 0, "wrote": [], "errors": []}

    def run_one(item):
        if remaining() <= 1.5:
            return item, None, "budget"
        try:
            rec = fetch(item["code"], item["market"], item["name"], funds)
            return item, rec, None
        except Exception as e:  # noqa: BLE001
            return item, None, f"{type(e).__name__}: {e}"

    if workers > 1 and len(planned) > 1:
        with concurrent.futures.ThreadPoolExecutor(max_workers=min(workers, 8)) as pool:
            futures = [pool.submit(run_one, item) for item in planned]
            outcomes = []
            for fut in concurrent.futures.as_completed(futures):
                outcomes.append(fut.result())
    else:
        outcomes = [run_one(item) for item in planned]

    for item, rec, err in outcomes:
        code = item["code"]
        if err == "budget":
            results["skipped"] += 1
            continue
        if rec is None:
            results["failed"] += 1
            results["errors"].append(f"{code}: {err}")
            log(f"[warn] 신호 데이터 실패 {code}({item.get('name')}): {err}")
            continue
        prev = records.get(code) or {}
        if keep_good and prev.get("bars") and len(rec.get("bars") or []) < 20:
            results["failed"] += 1
            results["errors"].append(f"{code}: 봉 부족({len(rec.get('bars') or [])})")
            continue
        records[code] = rec
        results["ok"] += 1
        if not dry:
            results["wrote"].append(write_record(rec))

    if dry:
        log(f"[signal] --check: 파일을 쓰지 않았습니다 (성공 {results['ok']} · 실패 {results['failed']})")
        return results

    manifest_path = write_manifest(coverage, records, results["wrote"])
    paths = results["wrote"] + [manifest_path]
    stage_for_ci(paths)
    results["manifest"] = manifest_path
    log(f"[signal] 완료: 성공 {results['ok']} · 실패 {results['failed']} · 건너뜀 {results['skipped']}"
        f" · 소요 {time.monotonic() - start:.0f}초")
    return results


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="매수·매도 시점 분석 데이터 수집")
    parser.add_argument("--limit", type=int, default=None, help="이번 실행에서 받을 종목 수")
    parser.add_argument("--budget", type=float, default=None, help="이번 실행 예산(초)")
    parser.add_argument("--force", action="store_true", help="신선도와 무관하게 다시 받기")
    parser.add_argument("--workers", type=int, default=1, help="동시 요청 수(기본 1)")
    parser.add_argument("--check", action="store_true", help="파일을 쓰지 않고 현황만 출력")
    args = parser.parse_args(argv)

    if args.check:
        config = live.load_config()
        universe = live.load_json(UNIVERSE_PATH, {}) or {}
        manifest = load_manifest()
        coverage = build_coverage(config, universe, manifest)
        have = sum(1 for item in coverage if (load_record(item["code"]) or {}).get("bars"))
        print(json.dumps({
            "coverage": len(coverage),
            "with_bars": have,
            "manifest_updated": manifest.get("generated_at"),
            "budget": SIGNAL_BUDGET_SECONDS,
        }, ensure_ascii=False, indent=2))
        return 0

    result = collect(limit=args.limit, budget=args.budget, force=args.force,
                     workers=args.workers, dry=False)
    print(json.dumps({"ok": result["ok"], "failed": result["failed"],
                      "skipped": result["skipped"], "errors": result["errors"][:5]},
                     ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
