#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""SAVE(세이브티커) 스냅샷 재생성 — 로그인 브라우저 세션에서 받은 API 응답 PDF/JSON 으로.

saveticker.com 은 Cloudflare 봇 차단 뒤에 있어 GitHub Actions 의 익명 요청으로는
API(/api/news/top-stories, /api/news/list, /api/calendar/events)가 403 으로 막힌다.
그래서 SAVE 데이터는 '사용자로 로그인된 브라우저 세션'에서 받아 이 스크립트로
data/save_latest.json 을 다시 만들고, news_latest.json 에 병합한다.

브라우저에서 받는 방법(에이전트용):
  1. https://www.saveticker.com/profile 에서 로그인 확인(카카오 계정)
  2. 아래 4개 URL 을 차례로 열고 각각  browser_pdf 로 저장
       /api/news/top-stories
       /api/news/list?page=1&page_size=20&sort=created_at_desc&keywords=<관심 키워드>
       /api/news/list?page=1&page_size=20&sort=created_at_desc&tickers=<관심 티커>
       /api/calendar/events?start_date=<오늘>&end_date=<+14일>
  3. 이 스크립트 실행
       python3 scripts/save_snapshot_from_pdfs.py \
         --top browser/save_top.pdf --keywords browser/save_kw.pdf \
         --tickers browser/save_tkr.pdf --calendar browser/save_cal.pdf

PDF 는 브라우저 인쇄본이라 페이지 경계마다 꼬리표("pretty print 적용")가 붙고
공백이 제어문자(\\x01)로 바뀌며 줄바꿈이 섞인다 — extract_save_json() 이 그것을 복원한다.
JSON 파일을 직접 넘겨도 된다(--top path.json ...).
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SPEC = importlib.util.spec_from_file_location(
    "collect_live_data", os.path.join(ROOT, "scripts", "collect_live_data.py"))
cld = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(cld)

_PDF_TAIL = re.compile(r"pretty[\s\x01]*print[\s\x01]*적용")


def repair_text(text: str) -> str:
    """브라우저 인쇄본 텍스트 복원: 제어문자 공백화 + 인쇄 꼬리표/줄바꿈 제거."""
    text = _PDF_TAIL.sub("", text)  # 페이지 꼬리표는 어디에 있어도 제거
    text = text.replace("\x01", " ")
    return text.replace("\r", "").replace("\n", "")


def extract_save_json(path: str) -> dict:
    """PDF(브라우저 인쇄본) 또는 JSON 파일에서 API 응답 JSON 을 꺼낸다."""
    if path.lower().endswith(".json"):
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    try:
        import pypdf  # noqa: PLC0415
    except ImportError as e:  # pragma: no cover - 환경 문제
        raise SystemExit("pypdf 가 필요합니다: pip install pypdf") from e
    reader = pypdf.PdfReader(path)
    text = "".join(repair_text(page.extract_text() or "") for page in reader.pages)
    start = text.find("{")
    if start < 0:
        raise SystemExit(f"JSON 을 찾지 못했습니다: {path}")
    obj, _end = json.JSONDecoder().raw_decode(text[start:])
    if not isinstance(obj, dict):
        raise SystemExit(f"예상하지 못한 형식: {path}")
    return obj


def rebuild(top: str, keywords: str | None, tickers: str | None, calendar: str | None,
            merged: bool = True, check_url: bool = False) -> dict:
    cfg = cld.load_config()
    bundle = {
        "top": extract_save_json(top) if top else {},
        "keywords": extract_save_json(keywords) if keywords else {},
        "tickers": extract_save_json(tickers) if tickers else {},
        "calendar": extract_save_json(calendar) if calendar else {},
        "via": "browser-session",
    }
    snapshot = cld.build_save_snapshot(bundle, cfg, cld.iso_kst(cld.now_kst()))
    snapshot["status"] = "live"
    snapshot["errors"] = []
    snapshot["account"] = "SAVE 로그인 세션(카카오)"
    snapshot["note"] = ("saveticker.com 은 Cloudflare 봇 차단 뒤에 있어 GitHub Actions(익명 요청)에서는 "
                        "403 입니다. 이 스냅샷은 사용자 로그인 브라우저 세션에서 /api/news/* 응답을 받아 만듭니다.")
    if check_url:
        snapshot["check_url"] = check_url
    with open(cld.SAVE_PATH, "w", encoding="utf-8") as f:
        json.dump(snapshot, f, ensure_ascii=False, indent=1)
        f.write("\n")
    if merged:
        news = cld.load_json(cld.NEWS_PATH, {})
        if news:
            news = cld.merge_save_news(news, snapshot, cfg)
            with open(cld.NEWS_PATH, "w", encoding="utf-8") as f:
                json.dump(news, f, ensure_ascii=False, indent=1)
                f.write("\n")
    return snapshot


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="SAVE 스냅샷 재생성(브라우저 세션 PDF → data/save_latest.json)")
    parser.add_argument("--top", required=True, help="top-stories PDF/JSON")
    parser.add_argument("--keywords", help="news/list (keywords=…) PDF/JSON")
    parser.add_argument("--tickers", help="news/list (tickers=…) PDF/JSON")
    parser.add_argument("--calendar", help="calendar/events PDF/JSON")
    parser.add_argument("--check-url", default="", help="이 스냅샷을 확인한 SAVE 페이지 URL")
    parser.add_argument("--no-merge", action="store_true", help="news_latest.json 병합 생략")
    args = parser.parse_args(argv)

    snapshot = rebuild(args.top, args.keywords, args.tickers, args.calendar,
                       merged=not args.no_merge, check_url=args.check_url)
    print(json.dumps({
        "fetched_at": snapshot["fetched_at"],
        "articles": snapshot["count"],
        "top_stories": len(snapshot["top_stories"]),
        "calendar_events": len(snapshot["calendar"]),
        "keywords": [k for k in snapshot.get("interests", {}).get("keywords", [])],
        "tickers": snapshot.get("interests", {}).get("tickers", []),
        "path": cld.SAVE_PATH,
    }, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
