import json
import tempfile
import time
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from backend.main import create_app

MODELS = {"models": [
    {"id": "fast", "label": "Fast", "interface": "mock", "strategy": "random", "seed": 1},
    {"id": "oracle", "label": "Oracle", "interface": "mock", "strategy": "oracle", "seed": 2},
]}


def parse_sse(text):
    events = []
    for block in text.strip().split("\n\n"):
        fields = dict(line.split(": ", 1) for line in block.splitlines() if ": " in line and not line.startswith(":"))
        if "data" in fields:
            events.append((fields["id"], fields["event"], json.loads(fields["data"])))
    return events


class ApiTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        (root / "models.json").write_text(json.dumps(MODELS))
        self.client = TestClient(create_app(root / "models.json", root / "results"))
        self.root = root

    def tearDown(self):
        self.tmp.cleanup()

    def wait_done(self):
        for _ in range(200):
            if self.client.get("/api/benchmark/status").json()["status"] != "running":
                return
            time.sleep(0.05)
        self.fail("run did not finish")

    def test_student_from_env(self):
        import os
        old = {k: os.environ.get(k) for k in ("STUDENT_NAME", "STUDENT_ROLL_NO")}
        try:
            os.environ["STUDENT_NAME"], os.environ["STUDENT_ROLL_NO"] = "Test Name", "00-TEST"
            self.assertEqual(self.client.get("/api/student").json(), {"name": "Test Name", "roll": "00-TEST"})
            del os.environ["STUDENT_NAME"]
            self.assertIn("STUDENT_NAME", self.client.get("/api/student").json()["name"])
        finally:
            for k, v in old.items():
                os.environ.pop(k, None)
                if v is not None:
                    os.environ[k] = v

    def test_models_and_sets(self):
        self.assertEqual([m["id"] for m in self.client.get("/api/models").json()], ["fast", "oracle"])
        self.assertIn("primary", self.client.get("/api/sets").json())

    def test_full_run_stream_and_storage(self):
        r = self.client.post("/api/benchmark/start", json={"set": "primary", "n": 2, "seed": 1})
        self.assertEqual(r.json()["run_id"], "0001")
        self.wait_done()
        events = parse_sse(self.client.get("/api/benchmark/stream?run_id=0001").text)
        types = [e[1] for e in events]
        self.assertEqual(types[0], "run_start")
        self.assertEqual(types[-1], "run_end")
        self.assertEqual(types.count("trial_start"), 4)   # 2 puzzles x 2 models
        self.assertEqual(types.count("trial_end"), 4)
        self.assertEqual([e[0] for e in events], [str(i) for i in range(len(events))])
        # file artefacts + summary
        saved = self.client.get("/api/runs/0001").json()
        self.assertEqual(len(saved["trials"]), 4)
        self.assertEqual(saved["summary"]["oracle"]["success_rate_pct"], 100)
        lines = (self.root / "results/0001/events.jsonl").read_text().splitlines()
        self.assertEqual(len(lines), len(events))
        live = self.client.get("/api/runs/0001/live-summary").json()["summary"]
        self.assertEqual(live["oracle"]["trials"], 2)

    def test_recorded_run_listing_events_and_partial_summary(self):
        self.client.post("/api/benchmark/start", json={"set": "primary", "n": 3, "seed": 5})
        self.wait_done()
        info = self.client.get("/api/runs").json()[0]
        self.assertEqual((info["set"], info["n_puzzles"], info["trials"], info["replayable"]), ("primary", 3, 6, True))
        self.assertEqual(info["models"], ["Fast", "Oracle"])
        events = self.client.get("/api/runs/0001/events").json()
        self.assertEqual(events[0]["type"], "run_start")
        moves = [e for e in events if e["type"] == "move"]
        self.assertTrue(all(e["latency_s"] >= 0 and len(e["state_after"]) == 54 for e in moves))
        # statistics as they stood after the first 2 finished trials
        part = self.client.get("/api/runs/0001/live-summary?upto=2").json()["summary"]
        self.assertEqual({k: v["trials"] for k, v in part.items()}, {"fast": 1, "oracle": 1})
        full = self.client.get("/api/runs/0001/live-summary").json()["summary"]
        self.assertEqual(full["oracle"]["trials"], 3)
        self.assertEqual(self.client.get("/api/runs/0007/events").status_code, 404)

    def test_listing_survives_incomplete_run(self):
        d = self.root / "results" / "0001"
        d.mkdir(parents=True)
        (d / "events.jsonl").write_text('{"type": "run_start", "set": "warmup", "puzzles": [{}], "models": ["m"], '
                                        '"ts": "2026-01-01T00:00:00"}\n{"type": "move", "mod')  # truncated line
        info = self.client.get("/api/runs").json()[0]
        self.assertEqual((info["status"], info["models"], info["replayable"]), ("incomplete", ["m"], True))

    def test_resume_with_last_event_id(self):
        self.client.post("/api/benchmark/start", json={"set": "warmup", "n": 1})
        self.wait_done()
        full = parse_sse(self.client.get("/api/benchmark/stream").text)
        part = parse_sse(self.client.get("/api/benchmark/stream", headers={"Last-Event-ID": "4"}).text)
        self.assertEqual(part[0][0], "5")
        self.assertEqual(len(part), len(full) - 5)

    def test_second_run_gets_next_folder_and_concurrent_start_rejected(self):
        self.client.post("/api/benchmark/start", json={"set": "warmup", "n": 1})
        self.wait_done()
        slow = {"models": [{"id": "slow", "label": "Slow", "interface": "mock", "strategy": "oracle", "latency_s": 0.3}]}
        (self.root / "models.json").write_text(json.dumps(slow))
        r = self.client.post("/api/benchmark/start", json={"set": "warmup", "n": 1}).json()
        self.assertEqual(r["run_id"], "0002")
        self.assertEqual(self.client.post("/api/benchmark/start", json={"set": "warmup", "n": 1}).status_code, 409)
        self.assertEqual(self.client.post("/api/benchmark/stop").json(), {"stopping": True})
        self.wait_done()
        self.assertEqual(self.client.get("/api/benchmark/status").json()["status"], "stopped")
        listing = self.client.get("/api/runs").json()
        self.assertEqual([r["run_id"] for r in listing], ["0002", "0001"])
        self.assertEqual(listing[1]["status"], "finished")
        self.assertEqual(listing[0]["status"], "stopped")

    def test_validation_and_missing(self):
        self.assertEqual(self.client.post("/api/benchmark/start", json={"set": "nope"}).status_code, 422)
        self.assertEqual(self.client.get("/api/benchmark/stream?run_id=9999").status_code, 404)
        self.assertEqual(self.client.get("/api/runs/0042").status_code, 404)

    def test_missing_claude_cli_fails_fast_with_pointer_to_docs(self):
        (self.root / "models.json").write_text(json.dumps({"models": [
            {"id": "cc", "label": "CC", "interface": "claude_code", "launch": "auto",
             "claude_bin": "definitely-not-installed-claude"}]}))
        r = self.client.post("/api/benchmark/start", json={"set": "warmup", "n": 1})
        self.assertEqual(r.status_code, 500)
        self.assertIn("Claude Code CLI not found", r.json()["detail"])
        self.assertEqual(self.client.get("/api/benchmark/status").json()["status"], "idle")

    def test_missing_api_key_fails_fast(self):
        import os
        (self.root / "models.json").write_text(json.dumps({"models": [
            {"id": "x", "label": "X", "interface": "chat"}]}))
        old = os.environ.pop("OPENROUTER_API_KEY", None)
        try:
            r = self.client.post("/api/benchmark/start", json={"set": "warmup", "n": 1})
        finally:
            if old is not None:
                os.environ["OPENROUTER_API_KEY"] = old
        self.assertEqual(r.status_code, 500)
        self.assertIn("OPENROUTER_API_KEY", r.json()["detail"])


if __name__ == "__main__":
    unittest.main()
