"""Durable programme-level decisions and human examples for audience inference."""
from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing, contextmanager, suppress
import csv
import json
import logging
import os
from pathlib import Path
import re
import secrets
import sqlite3
import unicodedata

from filelock import FileLock, Timeout

from .audience_ai import TYPES, Provider

LOG = logging.getLogger(__name__)
STOP_WORDS = set("콘서트 공연 기념 서울 내한 앵콜 투어 전국투어 단독 올림픽공원 월드 팬미팅 concert tour live in seoul the encore world fan meeting".split())


def words(value):
    return {w for w in re.findall(r"[^\W_]+", unicodedata.normalize("NFKC", value).casefold())
            if len(w) > 1 and not w.isnumeric() and w not in STOP_WORDS}


def compact(value):
    return "".join(re.findall(r"[^\W_]+", unicodedata.normalize("NFKC", value).casefold()))


class Audience:
    def __init__(self, folder: Path, provider=None, catalogue=None):
        self.folder = Path(folder)
        self.folder.mkdir(parents=True, mode=0o700, exist_ok=True)
        self.provider = provider
        self.changed = lambda: None
        self.pool = ThreadPoolExecutor(3, thread_name_prefix="audience")
        with self.db() as db:
            db.execute("""CREATE TABLE IF NOT EXISTS decisions (
                programme TEXT PRIMARY KEY, status TEXT NOT NULL, label TEXT,
                title TEXT NOT NULL, artist TEXT NOT NULL DEFAULT '', facts TEXT NOT NULL DEFAULT '{}',
                model TEXT, job TEXT, updated INTEGER NOT NULL DEFAULT 0)""")
            if catalogue and catalogue.is_file():
                with catalogue.open(encoding="utf-8-sig", newline="") as stream:
                    db.executemany("""INSERT INTO decisions (programme,status,label,title,artist) VALUES (?,'catalog',?,?,?)
                        ON CONFLICT(programme) DO UPDATE SET label=excluded.label, title=excluded.title, artist=excluded.artist
                        WHERE decisions.status='catalog'""",
                        ((r["event_id"], r["audience_type"], r["title"], r["act"]) for r in csv.DictReader(stream)
                         if r["audience_type"] in TYPES))

    @classmethod
    def from_root(cls, root):
        return cls(Path(os.getenv("OLPARK_AUDIENCE_DIR") or root / "data/runtime/audience"),
                   Provider.configured(), root / "data/prepared/response/audience_types.csv")

    def admin_token(self):
        configured = os.getenv("OLPARK_ADMIN_TOKEN")
        if configured:
            return configured
        path = self.folder / "admin.token"
        with FileLock(str(path) + ".lock"):
            if not path.exists():
                with open(path, "x", opener=lambda p, flags: os.open(p, flags, 0o600)) as stream:
                    stream.write(secrets.token_urlsafe(32) + "\n")
            token = path.read_text().strip()
            if not token:
                raise ValueError("Empty administrator token")
            return token

    @contextmanager
    def db(self):
        with closing(sqlite3.connect(self.folder / "audience.sqlite3", timeout=10)) as db, db:
            db.row_factory = sqlite3.Row
            yield db

    def labels(self):
        with self.db() as db:
            return dict(db.execute("SELECT programme,label FROM decisions WHERE label IS NOT NULL"))

    def state(self, programme):
        with self.db() as db:
            row = db.execute("SELECT status,label,model FROM decisions WHERE programme=?", (programme,)).fetchone()
        if row:
            status = "unconfigured" if row["status"] == "failed" and not self.provider else row["status"]
            return {"status": status, "label": row["label"], "model": row["model"]}
        return {"status": "idle" if self.provider else "unconfigured", "label": None, "model": None}

    def memories(self, programme, facts):
        """Retrieve human decisions only. Rank artist matches first, then distinctive title words."""
        title = facts["title"]
        query = words(title)
        text = compact(title)
        intro = compact(facts.get("intro", ""))
        act = compact(facts.get("artist", ""))
        with self.db() as db:
            rows = db.execute("""SELECT programme,title,artist,label,status,facts FROM decisions
                WHERE status IN ('manual','catalog') AND programme != ? ORDER BY updated DESC, programme DESC""",
                (programme,)).fetchall()
        ranked = []
        for row in rows:
            artist = compact(row["artist"])
            match = bool(artist and (artist == act or (len(artist) >= 2 and (artist in text or artist in intro))))
            common = query & words(row["title"])
            earlier = json.loads(row["facts"])
            # A manual example can predate any known artist record. Retain its listing
            # so the next programme by the same production can retrieve it as well.
            production = any(earlier.get(k) and earlier[k] == facts.get(k) for k in ("producer", "organizer"))
            if match or common or production:
                score = (match, len(common), production, row["status"] == "manual")
                ranked.append((score, {"programme": row["programme"], "title": row["title"], "artist": row["artist"],
                                       "audience_type": row["label"], "source": row["status"],
                                       "venue": earlier.get("venue", ""), "date": earlier.get("date", "")}))
        ranked.sort(key=lambda pair: pair[0], reverse=True)
        return [entry for _, entry in ranked[:5]]

    def manual(self, programme, facts, label):
        if label not in TYPES:
            raise ValueError("Unknown audience label")
        with self.db() as db:
            db.execute("""INSERT INTO decisions(programme,status,label,title,artist,facts,updated)
                VALUES (?,'manual',?,?,?,?,unixepoch()) ON CONFLICT(programme) DO UPDATE SET
                status='manual',label=excluded.label,title=excluded.title,facts=excluded.facts,
                artist=coalesce(nullif(excluded.artist,''),decisions.artist),model=NULL,job=NULL,updated=unixepoch()""",
                (programme, label, facts["title"], facts.get("artist", ""), json.dumps(facts, ensure_ascii=False)))
        self.changed()
        return self.state(programme)

    def resolve(self, programme, facts, retry=False):
        state = self.state(programme)
        if state["label"] or state["status"] == "unconfigured" or (state["status"] == "failed" and not retry):
            return state
        # The OS releases this lock if the process dies. All listeners share one lock per
        # official programme, including while the work is queued or the HTTP call is active.
        lock = FileLock(self.folder / f"{programme}.lock", thread_local=False)
        try:
            lock.acquire(timeout=0)
        except Timeout:
            state = self.state(programme)
            return {**state, "status": "pending"} if state["status"] == "idle" else state
        handed_off = False
        try:
            job = secrets.token_hex(16)
            with self.db() as db:
                db.execute("BEGIN IMMEDIATE")
                row = db.execute("SELECT status,label FROM decisions WHERE programme=?", (programme,)).fetchone()
                if row and row["status"] == "pending" and (not retry or not self.provider):
                    # The previous process died after dispatch. Do not automatically rebill an ambiguous call.
                    db.execute("UPDATE decisions SET status='failed',job=NULL WHERE programme=?", (programme,))
                elif not row or (not row["label"] and retry):
                    db.execute("""INSERT INTO decisions(programme,status,title,job) VALUES (?,'pending',?,?)
                        ON CONFLICT(programme) DO UPDATE SET status='pending',job=excluded.job""",
                        (programme, facts["title"], job))
                else:
                    return self.state(programme)
            if row and row["status"] == "pending" and (not retry or not self.provider):
                return self.state(programme)
            self.pool.submit(self._run, programme, facts, job, lock)
            handed_off = True
        finally:
            # Only the submitted worker may release a live job's lock.
            if not handed_off:
                lock.release()
        return self.state(programme)

    def _owns(self, programme, job):
        with self.db() as db:
            return db.execute("SELECT 1 FROM decisions WHERE programme=? AND job=?", (programme, job)).fetchone() is not None

    async def _classify(self, programme, facts, job):
        if not self._owns(programme, job):
            return
        task = asyncio.create_task(self.provider(facts, self.memories(programme, facts)))
        try:
            async with asyncio.timeout(100):
                while not task.done():
                    await asyncio.wait({task}, timeout=0.2)
                    if not self._owns(programme, job):
                        return
                label, model = task.result()
                if label not in TYPES:
                    raise ValueError("Invalid classification")
                with self.db() as db:
                    saved = db.execute("""UPDATE decisions SET status='ai',label=?,model=?,job=NULL,updated=unixepoch()
                        WHERE programme=? AND job=?""", (label, model, programme, job)).rowcount
                if saved:
                    self.changed()
        finally:
            if not task.done():
                task.cancel()
                with suppress(asyncio.CancelledError):
                    await task

    def _run(self, programme, facts, job, lock):
        try:
            asyncio.run(self._classify(programme, facts, job))
        except Exception as issue:
            LOG.warning("Audience inference for %s failed (%s, HTTP %s)", programme, type(issue).__name__,
                        getattr(getattr(issue, "response", None), "status_code", "-"))
            with self.db() as db:
                db.execute("UPDATE decisions SET status='failed',job=NULL WHERE programme=? AND job=?", (programme, job))
        finally:
            lock.release()
