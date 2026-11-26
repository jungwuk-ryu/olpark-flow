import asyncio
from concurrent.futures import ThreadPoolExecutor
import json
import multiprocessing
import os
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

import httpx

from olpark_demand.audience import Audience
from olpark_demand.audience_ai import Provider, listing

SHOW = {"title": "아티스트 별밤 첫 콘서트", "artist": "별밤", "venue": "올림픽홀"}


def request_from_another_process(path, output):
    async def unexpected_call(*args):
        raise AssertionError("Another process already owns this programme")
    store = Audience(path, unexpected_call)
    output.put(store.resolve("42", SHOW))


class AudienceTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.path = Path(temp.name)
        self.started, self.finish, self.cancelled = threading.Event(), threading.Event(), threading.Event()
        self.calls = []
        self.store = self.open(self.classify)

    def open(self, fn=None):
        store = Audience(self.path, fn)
        self.addCleanup(store.pool.shutdown)
        return store

    async def classify(self, facts, memories):
        self.calls.append(memories)
        self.started.set()
        try:
            while not self.finish.is_set():
                await asyncio.sleep(.01)
        except asyncio.CancelledError:
            self.cancelled.set()
            # Simulate a late response after the client disconnected.
        return "idol", "test-model"

    def test_one_call_across_simultaneous_readers_processes_and_restart(self):
        other = self.open(self.classify)
        try:
            with ThreadPoolExecutor(16) as pool:
                results = list(pool.map(lambda i: [self.store, other][i % 2].resolve("42", SHOW), range(32)))
            self.assertTrue(self.started.wait(2))
            self.assertEqual({r["status"] for r in results}, {"pending"})
            context = multiprocessing.get_context("spawn")
            output = context.Queue()
            process = context.Process(target=request_from_another_process, args=(self.path, output))
            process.start()
            process.join(5)
            self.assertEqual(process.exitcode, 0)
            self.assertEqual(output.get(timeout=1)["status"], "pending")
        finally:
            self.finish.set()
        self.store.pool.shutdown()
        other.pool.shutdown()
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(self.open().state("42"), {"status": "ai", "label": "idol", "model": "test-model"})
        self.assertEqual(self.open(self.classify).resolve("42", SHOW)["status"], "ai")
        self.assertEqual(len(self.calls), 1)

    def test_manual_choice_cancels_and_survives_stale_model_result(self):
        self.store.resolve("42", SHOW)
        self.assertTrue(self.started.wait(2))
        other = self.open(self.classify)
        other.manual("42", SHOW, "family")
        try:
            self.assertTrue(self.cancelled.wait(2))
        finally:
            self.finish.set()
        self.store.pool.shutdown()
        self.assertEqual(other.resolve("42", SHOW), {"label": "family", "model": None, "status": "manual"})
        next_show = {"title": "별밤과 함께하는 겨울", "artist": "별밤"}
        other.resolve("43", next_show)
        other.pool.shutdown()
        self.assertEqual(self.calls[1][0]["audience_type"], "family")
        # The classifier can disregard the retrieved family example.
        self.assertEqual(other.state("43")["label"], "idol")
        self.assertEqual(other.memories("44", next_show)[0]["programme"], "42")
        self.assertNotIn("43", [r["programme"] for r in other.memories("44", next_show)])

    def test_historical_manual_and_latest_correction_reused_without_calls(self):
        csv = self.path / "history.csv"
        csv.write_text("event_id,audience_type,act,title\n42,senior,별밤,별밤 데뷔\n", encoding="utf-8")
        store = Audience(self.path, self.classify, csv)
        self.addCleanup(store.pool.shutdown)
        self.assertEqual(store.resolve("42", SHOW)["status"], "catalog")
        store.manual("42", SHOW, "family")
        restarted = Audience(self.path, self.classify, csv)
        self.addCleanup(restarted.pool.shutdown)
        self.assertEqual(restarted.resolve("42", SHOW)["label"], "family")
        self.assertEqual(self.calls, [])
        self.assertEqual(restarted.memories("43", {"title": "별밤 뮤지컬"})[0]["audience_type"], "family")
        self.assertEqual(restarted.memories("43", {"title": "다른 공연"}), [])

    def test_no_key_and_manual_selection(self):
        store = self.open()
        self.assertEqual(store.resolve("42", SHOW)["status"], "unconfigured")
        self.assertEqual(store.manual("42", SHOW, "senior")["label"], "senior")
        self.assertEqual(self.store.resolve("42", SHOW)["status"], "manual")

    def test_failure_and_orphan_need_explicit_retry(self):
        async def broken(*args):
            raise ValueError("invalid response")
        bad = self.open(broken)
        bad.resolve("42", SHOW)
        bad.pool.shutdown()
        self.assertEqual(self.store.resolve("42", SHOW)["status"], "failed")
        with self.store.db() as db:
            db.execute("INSERT INTO decisions(programme,status,title,job) VALUES ('43','pending','show','dead')")
        self.assertEqual(self.store.resolve("43", SHOW)["status"], "failed")
        self.assertEqual(self.calls, [])
        self.finish.set()
        self.store.resolve("43", SHOW, retry=True)
        self.store.pool.shutdown()
        self.assertEqual(self.store.state("43")["status"], "ai")

    def test_input_uses_calendar_fields_and_fixed_poster_origin(self):
        data = listing({"title": "공연", "seeAge": "8세", "muf": "제작", "spns": "주최",
                        "content": "<p>공연 &amp; 축제<img alt='별밤'></p><script>ignore()</script>",
                        "fileNm": "202611.jpg"})
        self.assertEqual((data["age"], data["producer"], data["organizer"]), ("8세", "제작", "주최"))
        self.assertEqual(data["intro"], "공연 & 축제 별밤")
        self.assertEqual(data["poster"], "https://www.ksponco.or.kr/olympicpark/attachFiles/eventInfo/202611.jpg")
        self.assertEqual(listing({"fileNm": "https://another.example/p.jpg"})["poster"], "")


class ProviderTests(unittest.IsolatedAsyncioTestCase):
    async def test_provider_multimodal_search_and_output(self):
        answer = '{"audience_type":"musical"}'
        examples = {
            "openai": {"output": [{"type": "web_search_call", "status": "completed"}, {"type": "message", "content": [{"type": "output_text", "text": answer}]}]},
            "anthropic": {"content": [{"type": "web_search_tool_result", "content": []}, {"type": "text", "text": "검색 결과"}, {"type": "text", "text": answer}]},
            "gemini": {"candidates": [{"groundingMetadata": {"webSearchQueries": ["공연"]}, "content": {"parts": [{"text": "```json\n" + answer + "\n```"}]}}]},
        }
        for name, response in examples.items():
            with self.subTest(provider=name):
                def api(request):
                    args = json.loads(request.content)
                    self.assertTrue(args["tools"])
                    self.assertIn("aW1hZ2U=", str(args))
                    self.assertIn("공연", str(args))
                    return httpx.Response(200, json=response)
                async with httpx.AsyncClient(transport=httpx.MockTransport(api)) as client:
                    label, _ = await Provider(name, "model", "test").classify(client, "공연", ("image/png", "aW1hZ2U="))
                    self.assertEqual(label, "musical")

    async def test_luna_checks_model_availability_without_duplicate_generation(self):
        calls = []
        def api(request):
            calls.append(request.method)
            if request.method == "GET":
                return httpx.Response(404)
            self.assertEqual(json.loads(request.content)["model"], "gpt-6-luna")
            return httpx.Response(200, json={"output": [{"type": "web_search_call", "status": "completed"}, {"type": "message", "content": [
                {"type": "output_text", "text": '{"audience_type":"general"}'}]}]})
        async with httpx.AsyncClient(transport=httpx.MockTransport(api)) as client:
            result = await Provider("openai", "gpt-6.1-luna", "test", True).classify(client, "title", None)
        self.assertEqual(result, ("general", "gpt-6-luna"))
        self.assertEqual(calls, ["GET", "POST"])

    def test_explicit_provider_or_one_available_key(self):
        for name, key, model in [("openai", "OPENAI_API_KEY", "gpt-6.1-luna"),
                                 ("anthropic", "ANTHROPIC_API_KEY", "claude-haiku-5-5"),
                                 ("gemini", "GEMINI_API_KEY", "gemini-4-hydrogen")]:
            with patch.dict(os.environ, {key: "x"}, clear=True):
                self.assertEqual(Provider.configured().model, model)
        with patch.dict(os.environ, {}, clear=True):
            self.assertIsNone(Provider.configured())
