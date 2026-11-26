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
                 newest=newest_bundle):
        self.root, self.days = Path(root), days
        self.fetch, self.clock, self.make_builder, self.stored, self.newest = fetch, clock, make_builder, stored, newest
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

    def refresh(self) -> dict:
        now = self.clock()
        first = now.strftime("%Y-%m-%d")
        sessions, calendar, fetched, note = self.sessions(now, first)
        if self.builder is None or self.builder.bundle != self.newest(self.root):
            self.builder = self.make_builder(self.root)     # a newer bundle was published
        built = self.builder.build(sessions, now, first, self.days)
        source = {"kind": "server", "calendar": calendar, "provider": dashboard.CALENDAR_PROVIDER,
                  "fetchedAt": fetched.replace(microsecond=0).isoformat() if fetched else None,
                  "days": self.days, "leftOut": len(built["leftOut"]), "leftOutEvents": built["leftOut"],
                  "unlabelled": built["unlabelled"], "note": note}
        self.snapshot = {"events": built["events"], "predictions": built["predictions"],
                         "evenings": built.get("evenings", []), "evidence": built.get("evidence"), "source": source}
        return self.snapshot

    def refresh_safely(self) -> None:
        try:
            self.refresh()
            self.failure = None
        except Exception as issue:
            traceback.print_exc()
            # The message can hold local paths; the dashboard is told only that the refresh failed and when.
            self.failure = {"at": self.clock().replace(microsecond=0).isoformat(), "kind": type(issue).__name__}
        finally:
            self.ready.set()

    def keep_fresh(self, minutes: float) -> None:
        while True:
            self.refresh_safely()
            time.sleep(max(60.0, minutes * 60))

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

    def api(self, path: str) -> None:
        forecasts = self.forecasts
        if path == "/api/status":
            return self.answer(200, forecasts.status())
        forecasts.ready.wait(self.wait_seconds)
        snapshot = forecasts.snapshot
        if snapshot is None:
            return self.answer(503, {"error": "예측을 아직 만들지 못했습니다.", "lastFailure": forecasts.failure})
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
    forecasts = Forecasts(args.root, args.days)
    threading.Thread(target=forecasts.keep_fresh, args=(args.refresh_minutes,), daemon=True).start()
    servers = serve(forecasts, args.directory, ["127.0.0.1", *args.bind], args.port)
    print("\n".join(f"http://{server.server_address[0]}:{args.port}" for server in servers), flush=True)
    try:
        threading.Event().wait()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
