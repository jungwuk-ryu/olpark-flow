import contextlib
from datetime import datetime, timedelta
import io
import json
from pathlib import Path
import tempfile
import threading
import unittest
from urllib.error import HTTPError
from urllib.request import urlopen

import pandas as pd

from olpark_demand import dashboard, server

ROOT = Path(__file__).resolve().parents[1]
LOCAL_DATA = (ROOT / "data/prepared/response/sessions.parquet").is_file()
NOW = datetime(2026, 11, 26, 17, 0, tzinfo=dashboard.KST)


class StubBuilder:
    """Stands in for the forecast builder: one event per session it is given."""

    bundle = "stub-bundle"
    made = 0

    def __init__(self, root):
        type(self).made += 1

    def build(self, sessions, now, first, days):
        ids = [f"kspo-{i}" for i in range(len(sessions))]
        return {"events": {"schemaVersion": 1, "events": [{"id": i, "name": "공연 A"} for i in ids]},
                "predictions": {"schemaVersion": 1, "generatedAt": now.isoformat(),
                                "model": {"name": "stub", "version": self.bundle},
                                "predictions": [{"eventId": i} for i in ids]},
                "evenings": [{"date": "2026-11-27", "eventIds": ids}], "evidence": {"track": None},
                "leftOut": [], "unlabelled": ids[:1]}


def forecasts(fetch, stored=None, root=ROOT, builder=StubBuilder, newest=lambda root: "stub-bundle"):
    return server.Forecasts(root, 21, fetch=fetch, clock=lambda: NOW, make_builder=builder,
                            stored=stored or (lambda root: pd.DataFrame({"session_id": ["kept"]})), newest=newest)


def patched_sessions(test, table):
    """Replace the calendar parsing, which needs the local data archive, by a fixed table."""
    original = dashboard.sessions_from_facts
    dashboard.sessions_from_facts = lambda facts, root: table
    test.addCleanup(setattr, dashboard, "sessions_from_facts", original)


class RefreshTests(unittest.TestCase):
    def test_the_calendar_is_asked_for_the_months_of_the_window(self):
        asked = []
        patched_sessions(self, pd.DataFrame({"session_id": ["a", "b"]}))
        state = forecasts(lambda month: asked.append(month) or [])
        snapshot = state.refresh()
        self.assertEqual(asked, ["2026-11", "2026-12"])
        self.assertEqual(snapshot["source"]["kind"], "server")
        self.assertEqual(snapshot["source"]["calendar"], "api")
        self.assertEqual(snapshot["source"]["fetchedAt"], "2026-11-26T17:00:00+09:00")
        self.assertIsNone(snapshot["source"]["note"])
        self.assertEqual(snapshot["source"]["unlabelled"], ["kspo-0"])
        self.assertEqual(len(snapshot["events"]["events"]), 2)

    def test_the_stored_table_stands_in_when_the_calendar_does_not_answer(self):
        def fetch(month):
            raise TimeoutError("no answer")

        state = forecasts(fetch, root=Path(tempfile.mkdtemp(prefix="olpark-server-")))
        with contextlib.redirect_stderr(io.StringIO()):
            snapshot = state.refresh()
        self.assertEqual(snapshot["source"]["calendar"], "stored")
        self.assertIn("저장해 둔 달력", snapshot["source"]["note"])
        self.assertIsNone(snapshot["source"]["fetchedAt"])
        self.assertEqual([e["id"] for e in snapshot["events"]["events"]], ["kspo-0"])

    def test_a_failed_refresh_keeps_the_previous_answer(self):
        patched_sessions(self, pd.DataFrame({"session_id": ["a"]}))
        state = forecasts(lambda month: [])
        state.refresh_safely()
        first = state.snapshot
        self.assertIsNone(state.failure)

        def broken(sessions, now, first, days):
            raise RuntimeError("/private/path/that/must/not/leak")

        state.builder.build = broken
        with contextlib.redirect_stderr(io.StringIO()):
            state.refresh_safely()
        self.assertIs(state.snapshot, first)
        self.assertEqual(state.failure, {"at": "2026-11-26T17:00:00+09:00", "kind": "RuntimeError"})
        self.assertNotIn("private", json.dumps(state.status()))
        self.assertTrue(state.ready.is_set())

    def test_the_builder_is_reopened_only_when_a_newer_bundle_is_published(self):
        patched_sessions(self, pd.DataFrame({"session_id": ["a"]}))
        # The first refresh opens the builder without asking; the next two compare its bundle with the newest.
        names = ["stub-bundle", "newer-bundle"]
        StubBuilder.made = 0
        state = forecasts(lambda month: [], newest=lambda root: names.pop(0))
        state.refresh()
        state.refresh()
        self.assertEqual(StubBuilder.made, 1)
        state.refresh()
        self.assertEqual(StubBuilder.made, 2)


class HttpTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.folder = Path(tempfile.mkdtemp(prefix="olpark-dist-"))
        (cls.folder / "index.html").write_text("<!doctype html><title>OLPARK FLOW</title>", encoding="utf-8")
        (cls.folder / "assets").mkdir()
        (cls.folder / "assets/app.js").write_text("console.log(1)", encoding="utf-8")
        original = dashboard.sessions_from_facts
        dashboard.sessions_from_facts = lambda facts, root: pd.DataFrame({"session_id": ["a", "b"]})
        cls.addClassCleanup(setattr, dashboard, "sessions_from_facts", original)
        cls.state = forecasts(lambda month: [])
        cls.state.refresh_safely()
        quiet = server.Handler.log_message
        server.Handler.log_message = lambda *args: None
        cls.addClassCleanup(setattr, server.Handler, "log_message", quiet)
        cls.servers = server.serve(cls.state, cls.folder, ["127.0.0.1"], 0)
        cls.addClassCleanup(lambda: [(s.shutdown(), s.server_close()) for s in cls.servers])
        cls.base = f"http://127.0.0.1:{cls.servers[0].server_address[1]}"

    def get(self, path):
        with urlopen(self.base + path, timeout=10) as response:
            return response.status, response.headers, response.read()

    def json(self, path):
        status, headers, body = self.get(path)
        self.assertEqual(status, 200)
        self.assertEqual(headers["Content-Type"], "application/json; charset=utf-8")
        self.assertEqual(headers["Cache-Control"], "no-store")
        return json.loads(body)

    def test_bundle_holds_events_predictions_and_source(self):
        bundle = self.json("/api/bundle")
        self.assertEqual(set(bundle), {"events", "predictions", "evenings", "evidence", "source"})
        self.assertEqual(self.json("/api/evenings"), {"evenings": bundle["evenings"], "evidence": bundle["evidence"]})
        self.assertEqual(bundle["source"]["calendar"], "api")
        self.assertEqual(bundle["events"], self.json("/api/events"))
        self.assertEqual(bundle["predictions"], self.json("/api/predictions"))

    def test_one_prediction_by_event_id(self):
        self.assertEqual(self.json("/api/predictions/kspo-1"), {"eventId": "kspo-1"})
        with self.assertRaises(HTTPError) as missing:
            self.get("/api/predictions/nothing")
        self.assertEqual(missing.exception.code, 404)

    def test_status_reports_the_last_refresh(self):
        status = self.json("/api/status")
        self.assertTrue(status["ready"])
        self.assertEqual(status["events"], 2)
        self.assertEqual(status["model"]["version"], "stub-bundle")
        self.assertIsNone(status["lastFailure"])

    def test_unknown_api_path_is_not_found(self):
        with self.assertRaises(HTTPError) as missing:
            self.get("/api/secrets")
        self.assertEqual(missing.exception.code, 404)

    def test_the_built_dashboard_is_served_without_listing_folders(self):
        status, headers, body = self.get("/")
        self.assertEqual(status, 200)
        self.assertIn(b"OLPARK FLOW", body)
        self.assertEqual(headers["Cache-Control"], "no-cache")
        self.assertEqual(self.get("/assets/app.js")[1]["Cache-Control"], "public, max-age=3600")
        with self.assertRaises(HTTPError) as listing:
            self.get("/assets/")
        self.assertEqual(listing.exception.code, 404)
        with self.assertRaises(HTTPError) as outside:
            self.get("/../server.py")
        self.assertEqual(outside.exception.code, 404)

    def test_a_request_before_the_first_build_waits_and_then_says_so(self):
        waiting = forecasts(lambda month: [])
        handler = type("Impatient", (server.Handler,), {"forecasts": waiting, "wait_seconds": 0.05})
        from functools import partial
        from http.server import ThreadingHTTPServer
        listener = ThreadingHTTPServer(("127.0.0.1", 0), partial(handler, directory=str(self.folder)))
        threading.Thread(target=listener.serve_forever, daemon=True).start()
        self.addCleanup(lambda: (listener.shutdown(), listener.server_close()))
        base = f"http://127.0.0.1:{listener.server_address[1]}"
        with self.assertRaises(HTTPError) as early:
            urlopen(base + "/api/bundle", timeout=10)
        self.assertEqual(early.exception.code, 503)
        with urlopen(base + "/api/status", timeout=10) as response:
            self.assertFalse(json.loads(response.read())["ready"])


@unittest.skipUnless(LOCAL_DATA, "needs the local data archive (data/ is not in the repository)")
class LocalForecastTests(unittest.TestCase):
    def test_posted_programmes_reach_the_answer_through_the_real_builder(self):
        now = datetime.now(dashboard.KST)
        day = now.date() + timedelta(days=6)
        weekday = "월화수목금토일"[day.weekday()]
        posted = [{"evtSeq": 990001, "title": "시험 공연", "plc": "KSPO DOME", "beginDt": day.isoformat(),
                   "endDt": day.isoformat(), "tm": f"{day:%m.%d}({weekday}) 6PM", "ndTm": "120분",
                   "regDate": "2026-11-01 10:00:00", "tktPrc": "", "seeAge": ""}]
        try:
            state = server.Forecasts(ROOT, 10, fetch=lambda month: posted, clock=lambda: now)
            snapshot = state.refresh()
        except FileNotFoundError as issue:
            self.skipTest(str(issue))
        self.assertEqual([e["id"] for e in snapshot["events"]["events"]], [f"kspo-{day:%Y%m%d}-1800"])
        self.assertEqual(snapshot["source"]["calendar"], "api")
        self.assertEqual(snapshot["predictions"]["model"]["version"], server.newest_bundle(ROOT))
        self.assertEqual(snapshot["source"]["unlabelled"], [f"kspo-{day:%Y%m%d}-1800"])
        self.assertNotIn("시험 공연", json.dumps(snapshot, ensure_ascii=False))


if __name__ == "__main__":
    unittest.main()
