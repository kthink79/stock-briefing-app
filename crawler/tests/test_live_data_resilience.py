# -*- coding: utf-8 -*-
"""collect_live_data.py 네트워크 타임아웃 · 부분 실패 복원력 테스트.

2026-11-22 03:17 KST live-data 실행(run 44934697099)이 10분간 멈춘 재발 방지용.
- 외부 호출 1회는 벽시계 상한 안에서 반드시 끝난다(응답 없는 서버·느린 drip 포함).
- 개별 종목/지표/키워드가 실패해도 실행은 계속되고, 직전 값이 유지된다.
"""
import importlib.util
import json
import os
import socket
import threading
import time
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
SPEC = importlib.util.spec_from_file_location(
    "collect_live_data", os.path.join(ROOT, "scripts", "collect_live_data.py"))
live = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(live)


class _LocalServer:
    """로컬 소켓 서버: 응답을 주지 않거나(무응답), 한 바이트씩 늦게 보낸다(drip)."""

    def __init__(self, drip: bool = False):
        self.drip = drip
        self._stop = threading.Event()
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._sock.bind(("127.0.0.1", 0))
        self._sock.listen(1)
        self.port = self._sock.getsockname()[1]
        self._thread = threading.Thread(target=self._serve, daemon=True)
        self._thread.start()

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}/slow"

    def _serve(self):
        try:
            conn, _ = self._sock.accept()
        except OSError:
            return
        try:
            if self.drip:
                conn.recv(1024)
                conn.sendall(b"HTTP/1.1 200 OK\r\nContent-Type: text/plain\r\n"
                             b"Content-Length: 100000\r\n\r\n")
                while not self._stop.is_set():
                    conn.sendall(b"x")
                    time.sleep(0.2)
            else:
                self._stop.wait(15)   # 받기만 하고 아무것도 보내지 않는다
        except OSError:
            pass
        finally:
            try:
                conn.close()
            except OSError:
                pass

    def close(self):
        self._stop.set()
        try:
            self._sock.close()
        except OSError:
            pass


class TestTimeoutPrimitives(unittest.TestCase):
    def test_socket_default_timeout_is_set(self):
        self.assertIsNotNone(socket.getdefaulttimeout())

    def test_run_with_timeout_returns_value(self):
        self.assertEqual(live.run_with_timeout(lambda: 42, 1.0), 42)

    def test_run_with_timeout_stops_slow_call(self):
        started = time.monotonic()
        with self.assertRaises(live.FetchTimeout):
            live.run_with_timeout(lambda: time.sleep(5), 0.2, label="slow")
        self.assertLess(time.monotonic() - started, 2.0)

    def test_run_with_timeout_from_worker_thread(self):
        """신호를 쓸 수 없는 워커 스레드에서도 시간 안에 포기한다."""
        result = {}

        def _worker():
            started = time.monotonic()
            try:
                live.run_with_timeout(lambda: threading.Event().wait(10), 0.3, label="blocked")
            except live.FetchTimeout:
                result["elapsed"] = time.monotonic() - started
                result["timeout"] = True
            except BaseException as e:  # noqa: BLE001
                result["error"] = e

        thread = threading.Thread(target=_worker)
        thread.start()
        thread.join(5)
        self.assertTrue(result.get("timeout"), msg=str(result.get("error")))
        self.assertLess(result["elapsed"], 2.0)


class TestHttpGetBounded(unittest.TestCase):
    def test_no_response_server_is_cut_off(self):
        server = _LocalServer(drip=False)
        try:
            started = time.monotonic()
            with self.assertRaises(RuntimeError):
                live.http_get(server.url, timeout=0.5, retries=0, max_seconds=1)
            self.assertLess(time.monotonic() - started, 5.0)
        finally:
            server.close()

    def test_drip_feed_server_is_cut_off(self):
        """바이트를 조금씩 보내 소켓 타임아웃을 피하는 서버도 벽시계 상한에서 끊긴다."""
        server = _LocalServer(drip=True)
        try:
            started = time.monotonic()
            with self.assertRaises(RuntimeError):
                live.http_get(server.url, timeout=5, retries=0, max_seconds=1)
            self.assertLess(time.monotonic() - started, 5.0)
        finally:
            server.close()

    def test_budget_exhausted_skips_network(self):
        original = live.STARTED_AT
        live.STARTED_AT = time.monotonic() - (live.RUN_BUDGET_SECONDS + 60)
        try:
            started = time.monotonic()
            with self.assertRaises(RuntimeError) as ctx:
                live.http_get("http://127.0.0.1:9/never", timeout=1, retries=0)
            self.assertIn("예산", str(ctx.exception))
            self.assertLess(time.monotonic() - started, 1.0)
        finally:
            live.STARTED_AT = original


class TestSectionResilience(unittest.TestCase):
    def setUp(self):
        self._orig_pace = live._pace_sleep
        self._orig_news = live.fetch_news_for
        live._pace_sleep = lambda seconds, deadline=None: None   # 테스트 속도

    def tearDown(self):
        live._pace_sleep = self._orig_pace
        live.fetch_news_for = self._orig_news

    def test_timed_isolates_failing_section(self):
        result = live._timed("boom", lambda: 1 / 0, lambda: {"ok": True})
        self.assertEqual(result, {"ok": True})

    def test_refresh_entry_keeps_previous_on_failure(self):
        def _fail(*a, **k):
            raise RuntimeError("blocked")
        live.fetch_news_for = _fail
        prev = {"query": "삼성전자", "articles": [{"title": "이전 기사"}],
                "fetched_at": "2020-01-01T00:00:00+09:00"}
        errors = []
        entry = live._refresh_entry(prev, "삼성전자", 0, 10, 5, errors, "삼성전자",
                                    True, {"fetched": 0})
        self.assertTrue(entry["stale"])
        self.assertEqual(entry["articles"], prev["articles"])
        self.assertEqual(len(errors), 1)

    def test_refresh_entry_respects_exhausted_deadline(self):
        calls = []
        live.fetch_news_for = lambda *a, **k: calls.append(1) or []
        prev = {"query": "HBM", "articles": [{"title": "이전"}],
                "fetched_at": "2020-01-01T00:00:00+09:00"}
        errors = []
        entry = live._refresh_entry(prev, "HBM", 0, 10, 5, errors, "HBM", True,
                                    {"fetched": 0},
                                    deadline=time.monotonic() - 1)
        self.assertEqual(calls, [])
        self.assertTrue(entry["stale"])
        self.assertTrue(any("예산" in e for e in errors))

    def test_collect_news_survives_all_failures(self):
        def _fail(*a, **k):
            raise live.FetchTimeout("시간 초과")
        live.fetch_news_for = _fail
        cfg = json.loads(json.dumps(live.DEFAULT_CONFIG))
        cfg["trace_keywords"] = cfg["trace_keywords"][:1]
        cfg["sector_news"] = cfg["sector_news"][:1]
        cfg["keyword_pool"] = cfg["keyword_pool"][:1]
        cfg["kr_stocks"] = {"005930": "삼성전자"}
        cfg["us_stocks"] = {"NVDA": "NVDA.O"}
        previous = {
            "keywords": {"엔비디아": {"query": "엔비디아", "articles": [{"title": "이전"}],
                                     "fetched_at": "2020-01-01T00:00:00+09:00"}},
            "sectors": [], "companies": {}, "pool": {},
        }
        news = live.collect_news(cfg, previous, {"005930": "삼성전자"})
        self.assertIn("keywords", news)
        self.assertTrue(news["errors"])

    def test_collect_market_survives_total_failure(self):
        names = ("fetch_domestic_stocks", "fetch_world_stocks", "fetch_domestic_index",
                 "fetch_world_index", "fetch_rates", "fetch_fx")
        originals = {n: getattr(live, n) for n in names}

        def _fail(*a, **k):
            raise live.FetchTimeout("시간 초과")
        for n in names:
            setattr(live, n, _fail)
        try:
            previous = {"market": {"rates": {}, "fx": {}},
                        "stocks": {"005930": {"price": 70000, "name": "삼성전자"}}}
            market = live.collect_market(live.DEFAULT_CONFIG, previous)
            self.assertTrue(market["errors"])
            self.assertEqual(market["stocks"]["005930"]["price"], 70000)
            self.assertTrue(market["stocks"]["005930"].get("stale"))
        finally:
            for n, fn in originals.items():
                setattr(live, n, fn)

    def test_refresh_fundamentals_time_budget(self):
        original = live.fetch_daum_fundamental

        def _slow(code, timeout=None):
            time.sleep(2)
            return None
        live.fetch_daum_fundamental = _slow
        try:
            started = time.monotonic()
            state = live.refresh_fundamentals([f"{i:06d}" for i in range(20)],
                                              {}, "2026-11-22",
                                              workers=2, budget_seconds=0.5)
            self.assertLess(time.monotonic() - started, 4.0)
            self.assertFalse(state["complete"])
        finally:
            live.fetch_daum_fundamental = original

    def test_watchdog_forces_exit(self):
        recorded = {}
        original = live.os._exit

        def _fake_exit(code):
            recorded["code"] = code
            raise SystemExit(code)
        live.os._exit = _fake_exit
        try:
            live.install_watchdog(0.2)
            time.sleep(0.6)
        finally:
            live.os._exit = original
        self.assertEqual(recorded.get("code"), 2)


if __name__ == "__main__":
    unittest.main()
