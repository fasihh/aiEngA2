import json
import unittest

import httpx

from backend.adapters.chat import ChatAdapter
from backend.adapters.decisions import DecisionsAdapter, build_request, parse_response
from backend.adapters.mock import MockAdapter
import tempfile
from pathlib import Path

from backend.benchmark import EventLog, new_run_dir, make_puzzles, run_benchmark, run_trial, summarize
from backend.cube import MOVES, SOLVED


def client_returning(*bodies, status=200):
    """httpx client that replies with the given JSON bodies in order (last repeats)."""
    calls = []

    def handler(request):
        calls.append(json.loads(request.content))
        body = bodies[min(len(calls) - 1, len(bodies) - 1)]
        if isinstance(body, Exception):
            raise body
        return httpx.Response(status, json=body)

    return httpx.Client(transport=httpx.MockTransport(handler)), calls


def chat_body(move):
    return {"choices": [{"message": {"content": json.dumps({"move": move})}}]}


class AdapterTests(unittest.TestCase):
    def test_chat_valid(self):
        client, calls = client_returning(chat_body("R'"))
        res = ChatAdapter("m", "key", client=client).get_move(SOLVED, ["U"])
        self.assertEqual((res.move, res.valid, res.retries), ("R'", True, 0))
        self.assertEqual(calls[0]["response_format"]["json_schema"]["schema"]["properties"]["move"]["enum"], MOVES)

    def test_chat_malformed_then_valid_counts_retry(self):
        client, _ = client_returning(chat_body("r'"), chat_body("R'"))
        res = ChatAdapter("m", "key", client=client).get_move(SOLVED, [])
        self.assertEqual((res.move, res.retries), ("R'", 1))
        self.assertEqual(len(res.errors), 1)

    def test_chat_always_malformed_is_not_converted(self):
        client, _ = client_returning(chat_body("turn right"))
        res = ChatAdapter("m", "key", client=client).get_move(SOLVED, [])
        self.assertIsNone(res.move)
        self.assertFalse(res.valid)
        self.assertEqual(res.retries, 1)

    def test_http_error_and_timeout_are_retried(self):
        client, calls = client_returning(httpx.ReadTimeout("slow"), chat_body("U"))
        res = ChatAdapter("m", "key", client=client).get_move(SOLVED, [])
        self.assertEqual((res.move, res.retries, len(calls)), ("U", 1, 2))
        client, _ = client_returning({"error": "boom"}, status=500)
        res = ChatAdapter("m", "key", client=client).get_move(SOLVED, [])
        self.assertIsNone(res.move)

    def test_decisions_request_shape(self):
        req = build_request("typesafe/jev-1.13", SOLVED, ["R"])
        self.assertEqual(req["state"], {"cube_facelets": SOLVED, "recent_moves": ["R"]})
        q = req["questions"]["next_move"]
        self.assertEqual(q["type"], "choice")
        self.assertEqual(list(q["criteria"]), MOVES)

    def test_decisions_parse_variants(self):
        self.assertEqual(parse_response({"answers": {"next_move": {"choice": "U2", "confidence": 0.7}}}), ("U2", 0.7))
        self.assertEqual(parse_response({"next_move": {"selected": "R", "probability": {"R": 0.4}}}), ("R", 0.4))

    def test_decisions_adapter(self):
        client, _ = client_returning({"answers": {"next_move": {"choice": "F'", "confidence": 0.9}}})
        res = DecisionsAdapter("m", "key", client=client).get_move(SOLVED, [])
        self.assertEqual((res.move, res.confidence), ("F'", 0.9))


class RunnerTests(unittest.TestCase):
    def test_puzzles_reproducible_and_exact(self):
        a, b = make_puzzles("primary", 5, seed=1), make_puzzles("primary", 5, seed=1)
        self.assertEqual(a, b)
        for p in a:
            self.assertTrue(p["optimal_is_exact"])
            self.assertTrue(1 <= p["optimal"] <= p["scramble_len"])
            self.assertNotEqual(p["initial_state"], SOLVED)

    def test_oracle_is_optimal_gap_zero(self):
        puzzles = make_puzzles("primary", 4, seed=2)
        trials = run_benchmark([MockAdapter("oracle", "oracle")], puzzles)
        for t in trials:
            self.assertTrue(t["solved"])
            self.assertEqual(t["gap"], 0)

    def test_move_limit_is_failure(self):
        class Stuck(MockAdapter):
            def _choose(self, state, recent_moves):
                return "U" if not recent_moves or recent_moves[-1] != "U" else "U'"

        puzzle = make_puzzles("extension", 1, seed=3)[0]
        t = run_trial(Stuck("stuck"), puzzle, max_moves=10)
        self.assertFalse(t["solved"])
        self.assertTrue(t["hit_move_limit"])
        self.assertEqual(t["steps_used"], 10)
        self.assertIsNone(t["gap"])

    def test_malformed_steps_use_budget_without_changing_cube(self):
        puzzle = make_puzzles("primary", 1, seed=4)[0]
        t = run_trial(MockAdapter("bad", "random", malformed_rate=1.0), puzzle, max_moves=5)
        self.assertEqual((t["moves_applied"], t["steps_used"], t["malformed_steps"]), (0, 5, 5))
        self.assertEqual(t["retries"], 5)

    def test_every_model_gets_same_start_state(self):
        puzzle = make_puzzles("warmup", 1, seed=5)[0]
        starts = []
        for m in ("a", "b"):
            run_trial(MockAdapter(m, "random", seed=1), puzzle,
                      on_event=lambda e: starts.append(e["state"]) if e["type"] == "trial_start" else None)
        self.assertEqual(starts[0], starts[1])

    def test_summary(self):
        puzzles = make_puzzles("primary", 3, seed=6)
        trials = run_benchmark([MockAdapter("oracle", "oracle"), MockAdapter("rand", "random", seed=1)], puzzles)
        s = summarize(trials)
        self.assertEqual(s["oracle"]["success_rate_pct"], 100)
        self.assertEqual(s["oracle"]["avg_optimality_gap"], 0)
        self.assertEqual(s["oracle"]["trials"], 3)

    def test_event_log_records_every_step_with_raw_and_state(self):
        puzzle = make_puzzles("primary", 1, seed=7)[0]
        with tempfile.TemporaryDirectory() as d:
            log = EventLog(Path(d) / "e.jsonl")
            t = run_trial(MockAdapter("m", "random", malformed_rate=0.3, seed=1), puzzle, log, max_moves=8)
            log.close()
            events = [json.loads(line) for line in (Path(d) / "e.jsonl").read_text().splitlines()]
        moves = [e for e in events if e["type"] == "move"]
        self.assertEqual(len(moves), t["steps_used"])
        self.assertTrue(all("raw" in e and len(e["state_after"]) == 54 and "ts" in e for e in moves))
        self.assertEqual([e["type"] for e in events][0::len(events) - 1], ["trial_start", "trial_end"])

    def test_run_dirs_are_numbered(self):
        with tempfile.TemporaryDirectory() as d:
            names = [new_run_dir(d).name for _ in range(3)]
            (Path(d) / "notes").mkdir()
            names.append(new_run_dir(d).name)
        self.assertEqual(names, ["0001", "0002", "0003", "0004"])


if __name__ == "__main__":
    unittest.main()
