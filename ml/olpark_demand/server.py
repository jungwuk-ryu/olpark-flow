"""Serve the dashboard and the forecasts it shows.

The server asks the park's public calendar for the coming weeks, builds one forecast per
posted session with the published event-response bundle, and answers the dashboard.
The calendar is asked again every ``--refresh-minutes``. If it does not answer, the stored
session table is used instead and the answer says so.

    GET /api/bundle                  events, predictions, the station's evenings and where they came from
    GET /api/events                  events.json of docs/data-contract.md
    GET /api/predictions             prediction.json
    GET /api/predictions/<event id>  one prediction
    GET /api/evenings                the station's hours on each date, and how earlier forecasts fared
    GET /api/status                  where the data came from and whether the last refresh worked

Every other path is a file of the built dashboard (``dist/`` at the repository root).
With no --bind the server is reachable from this computer alone; add an address of this
computer to open it to that network.

    python -m olpark_demand.server                          # http://127.0.0.1:4173
    python -m olpark_demand.server --bind 100.64.0.1        # also on that address
"""
from __future__ import annotations

import argparse
import hmac
from datetime import datetime
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import sys
import threading
import time
import traceback
from urllib.parse import unquote, urlsplit

from . import collect_calendar, dashboard
from .audience import Audience
from .audience_ai import TYPES
from .paths import repo_of
from .response_packet import BUNDLE_ROOT

ROOT = Path(__file__).resolve().parents[1]
KST = dashboard.KST
DEFAULT_PORT = 4173
DEFAULT_REFRESH_MINUTES = 60
# A request that arrives while the first forecasts are being built waits this long for them.
FIRST_BUILD_WAIT_SECONDS = 90


def newest_bundle(root: Path) -> str | None:
    """Name of the most recently fitted event-response bundle, as ``ResponseBundle.latest`` picks it."""
    folders = [m.parent for m in (Path(root) / BUNDLE_ROOT).glob("*/manifest.json") if (m.parent / "model.json").is_file()]
    if not folders:
        return None
    return max(folders, key=lambda f: json.loads((f / "manifest.json").read_text(encoding="utf-8"))["fitted_at"]).name


class Forecasts:
    """The latest events and predictions. ``refresh`` rebuilds them; a failed refresh keeps the previous ones."""

    def __init__(self, root: Path = ROOT, days: int = dashboard.DEFAULT_DAYS, *, fetch=collect_calendar.fetch_month,
                 clock=lambda: datetime.now(KST), make_builder=dashboard.Builder, stored=dashboard.stored_sessions,
                 newest=newest_bundle, audience=None):
        self.root, self.days = Path(root), days
        self.fetch, self.clock, self.make_builder, self.stored, self.newest = fetch, clock, make_builder, stored, newest
        self.audience = audience
        self.rebuild = threading.Event()
        self.cached_calendar = None
        self.programmes = {}
        if audience:
            audience.changed = self.rebuild.set
            self.admin_token = audience.admin_token()
        self.builder = None
        self.snapshot = None
        self.failure = None
        self.ready = threading.Event()

    def sessions(self, now: datetime, first: str):
        """Sessions of the window from the calendar as posted now, or from the stored table when it does not answer."""
        try:
            return dashboard.live_sessions(self.root, first, self.days, self.fetch), "api", now, None
        except Exception as issue:                          # no network, a timeout, an answer in another shape
            print(f"calendar: {type(issue).__name__}: {issue}", file=sys.stderr, flush=True)
            table = self.stored(self.root)
            prepared = self.root / "data/prepared/response/sessions.parquet"
            kept = datetime.fromtimestamp(prepared.stat().st_mtime, KST) if prepared.is_file() else None
            return table, "stored", kept, "공원 달력 API가 답하지 않아 저장해 둔 달력으로 만들었습니다."

    def refresh(self, *, cached=False) -> dict:
        now = self.clock()
        first = now.strftime("%Y-%m-%d")
        if not cached or self.cached_calendar is None:
            self.cached_calendar = self.sessions(now, first)
        sessions, calendar, fetched, note = self.cached_calendar
        if self.audience:
            sessions = sessions.copy()
            saved = sessions.event_id.astype(str).map(self.audience.labels())
            sessions["labelled"] = saved.notna()
            sessions["audience_type"] = saved.fillna(sessions.audience_type)
        if self.builder is None or self.builder.bundle != self.newest(self.root):
            self.builder = self.make_builder(self.root)     # a newer bundle was published
        built = self.builder.build(sessions, now, first, self.days)
        source = {"kind": "server", "calendar": calendar, "provider": dashboard.CALENDAR_PROVIDER,
                  "fetchedAt": fetched.replace(microsecond=0).isoformat() if fetched else None,
                  "days": self.days, "leftOut": len(built["leftOut"]), "leftOutEvents": built["leftOut"],
                  "unlabelled": built["unlabelled"], "note": note}
        if self.audience:
            listed = {e["programmeId"]: e for e in built["events"]["events"]}
            facts = sessions.attrs.get("audience_facts", {})
            programmes = {}
            for row in sessions.drop_duplicates("event_id").itertuples():
                key = str(row.event_id)
                if key in listed:
                    facts_for_show = dict(facts.get(key, {"title": listed[key]["name"], "venue": listed[key]["venueName"]}))
                    if isinstance(row.act, str) and row.act != key:
                        facts_for_show["artist"] = row.act
                    programmes[key] = (facts_for_show, row.audience_type if row.labelled else None)
            self.programmes = programmes
        self.snapshot = {"events": built["events"], "predictions": built["predictions"],
                         "evenings": built.get("evenings", []), "evidence": built.get("evidence"), "source": source}
        return self.snapshot

    def refresh_safely(self, *, cached=False) -> None:
        try:
            self.refresh(cached=cached)
            self.failure = None
        except Exception as issue:
            traceback.print_exc()
            # The message can hold local paths; the dashboard is told only that the refresh failed and when.
            self.failure = {"at": self.clock().replace(microsecond=0).isoformat(), "kind": type(issue).__name__}
        finally:
            self.ready.set()

    def keep_fresh(self, minutes: float) -> None:
        next_fetch = 0.0
        while True:
            self.rebuild.clear()
            cached = time.monotonic() < next_fetch
            self.refresh_safely(cached=cached)
            if not cached:
                next_fetch = time.monotonic() + max(60.0, minutes * 60)
            self.rebuild.wait(max(0.0, next_fetch - time.monotonic()))

    def status(self) -> dict:
        snapshot = self.snapshot
        return {"ready": snapshot is not None, "source": snapshot["source"] if snapshot else None,
                "events": len(snapshot["events"]["events"]) if snapshot else 0,
                "generatedAt": snapshot["predictions"]["generatedAt"] if snapshot else None,
                "model": snapshot["predictions"]["model"] if snapshot else None,
                "lastFailure": self.failure}


class Handler(SimpleHTTPRequestHandler):
    """The API under /api, the built dashboard everywhere else."""

    forecasts: Forecasts = None
    wait_seconds = FIRST_BUILD_WAIT_SECONDS

    def do_GET(self):
        path = unquote(urlsplit(self.path).path)
        if path == "/api" or path.startswith("/api/"):
            self.api(path.rstrip("/"))
        else:
            super().do_GET()

    def do_POST(self):
        path = unquote(urlsplit(self.path).path)
        if not path.startswith("/api/audience/"):
            return self.answer(404, {"error": "없는 주소입니다."})
        if self.headers.get_content_type() != "application/json":
            return self.answer(415, {"error": "JSON 요청이 필요합니다."})
        try:
            length = int(self.headers.get("Content-Length", 0))
            if not 0 < length <= 1024:
                raise ValueError()
            body = json.loads(self.rfile.read(length))
            if not isinstance(body, dict):
                raise ValueError()
        except (ValueError, UnicodeError):
            return self.answer(400, {"error": "요청을 읽을 수 없습니다."})
        self.audience(path, body)

    def audience(self, path, body=None):
        state = self.forecasts
        programme = path.removeprefix("/api/audience/")
        record = state.programmes.get(programme)
        if not state.audience or not record:
            return self.answer(404, {"error": "현재 공연 목록에서 찾을 수 없습니다."})
        facts, applied = record
        action = body.get("action") if body is not None else None
        if action in ("manual", "retry"):
            supplied = self.headers.get("Authorization", "").encode()
            if not hmac.compare_digest(supplied, ("Bearer " + state.admin_token).encode()):
                return self.answer(403, {"error": "관리자 키를 확인하세요."})
        if action == "manual":
            label = body.get("label")
            if not isinstance(label, str) or label not in TYPES:
                return self.answer(400, {"error": "관객층 일곱 가지 중 하나를 선택하세요."})
            result = state.audience.manual(programme, facts, label)
        elif action in ("resolve", "retry"):
            result = state.audience.resolve(programme, facts, retry=action == "retry")
        elif body is None:
            result = state.audience.state(programme)
        else:
            return self.answer(400, {"error": "알 수 없는 동작입니다."})
        result["applied"] = result["label"] == applied
        if result["label"] and not result["applied"]:
            state.rebuild.set()
        self.answer(200, result)

    def api(self, path: str) -> None:
        forecasts = self.forecasts
        if path == "/api/status":
            return self.answer(200, forecasts.status())
        forecasts.ready.wait(self.wait_seconds)
        snapshot = forecasts.snapshot
        if snapshot is None:
            return self.answer(503, {"error": "예측을 아직 만들지 못했습니다.", "lastFailure": forecasts.failure})
        if path.startswith("/api/audience/"):
            return self.audience(path)
        if path == "/api/bundle":
            return self.answer(200, snapshot)
        if path == "/api/events":
            return self.answer(200, snapshot["events"])
        if path == "/api/predictions":
            return self.answer(200, snapshot["predictions"])
        if path == "/api/evenings":
            return self.answer(200, {"evenings": snapshot["evenings"], "evidence": snapshot["evidence"]})
        if path.startswith("/api/predictions/"):
            wanted = path.removeprefix("/api/predictions/")
            for prediction in snapshot["predictions"]["predictions"]:
                if prediction["eventId"] == wanted:
                    return self.answer(200, prediction)
            return self.answer(404, {"error": "그 공연의 예측이 없습니다."})
        self.answer(404, {"error": "없는 주소입니다."})

    def answer(self, status: int, value) -> None:
        body = json.dumps(value, ensure_ascii=False, allow_nan=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def end_headers(self):
        path = urlsplit(getattr(self, "path", "")).path
        if path.startswith("/api"):
            self.send_header("Cache-Control", "no-store")
        else:
            # index.html names the hashed asset files, so it must not be cached; the assets may be.
            self.send_header("Cache-Control", "no-cache" if path in ("/", "/index.html") else "public, max-age=3600")
        super().end_headers()

    def list_directory(self, path):
        self.send_error(404)


def serve(forecasts: Forecasts, directory: Path, addresses: list[str], port: int) -> list[ThreadingHTTPServer]:
    """Start one listener per address. The caller keeps the process alive."""
    handler = partial(type("Dashboard", (Handler,), {"forecasts": forecasts}), directory=str(directory))
    servers = [ThreadingHTTPServer((address, port), handler) for address in dict.fromkeys(addresses)]
    for server in servers:
        threading.Thread(target=server.serve_forever, daemon=True).start()
    return servers


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--bind", action="append", default=[], help="another address to listen on (repeatable)")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--directory", type=Path, default=repo_of(ROOT) / "dist", help="the built dashboard")
    parser.add_argument("--days", type=int, default=dashboard.DEFAULT_DAYS, help="how many days ahead to list")
    parser.add_argument("--refresh-minutes", type=float, default=DEFAULT_REFRESH_MINUTES,
                        help="how often to ask the calendar again")
    parser.add_argument("--root", type=Path, default=ROOT)
    args = parser.parse_args()
    if not (args.directory / "index.html").is_file():
        print(f"{args.directory} has no build; only /api is served. Build the dashboard first (pnpm build).", file=sys.stderr, flush=True)
    forecasts = Forecasts(args.root, args.days, audience=Audience.from_root(args.root))
    threading.Thread(target=forecasts.keep_fresh, args=(args.refresh_minutes,), daemon=True).start()
    servers = serve(forecasts, args.directory, ["127.0.0.1", *args.bind], args.port)
    print("\n".join(f"http://{server.server_address[0]}:{args.port}" for server in servers), flush=True)
    try:
        threading.Event().wait()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
