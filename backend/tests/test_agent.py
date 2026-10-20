"""End-to-end tests of the MCP agent path: real MCP server, real HTTP backend, fake `claude`."""
import asyncio
import json
import os
import socket
import stat
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

import uvicorn
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from backend.adapters.claude_code import MCP_SERVER, ClaudeCodeMCPAdapter
from backend.agent_bridge import BRIDGE
from backend.benchmark import make_puzzles, run_trial
from backend.main import create_app
from backend.solver import solve_optimal

ROOT = Path(__file__).resolve().parents[2]
FAKE = Path(__file__).with_name("fake_claude.py")


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class Backend:
    """The real FastAPI app on a local port, so the MCP server process can reach it."""

    def __enter__(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        (root / "models.json").write_text(json.dumps({"models": []}))
        self.port = free_port()
        self.url = f"http://127.0.0.1:{self.port}"
        self.server = uvicorn.Server(uvicorn.Config(create_app(root / "models.json", root / "results"),
                                                    host="127.0.0.1", port=self.port, log_level="error"))
        self.thread = threading.Thread(target=self.server.run, daemon=True)
        self.thread.start()
        for _ in range(100):
            if self.server.started:
                return self
            time.sleep(0.05)
        raise RuntimeError("backend did not start")

    def __exit__(self, *exc):
        self.server.should_exit = True
        self.thread.join(timeout=5)
        self.tmp.cleanup()


async def external_agent(api_url, first_move=None, think_s=0.0, cycle=None):
    """Plays like a Claude Code session attached by hand: MCP client -> MCP server -> backend."""
    params = StdioServerParameters(command=sys.executable, args=[str(MCP_SERVER)],
                                   env={**os.environ, "CUBE_API_URL": api_url})
    async with stdio_client(params) as (r, w), ClientSession(r, w) as s:
        await s.initialize()
        tools = {t.name for t in (await s.list_tools()).tools}
        assert tools == {"get_cube_state", "make_move"}, tools
        while True:
            st = (await s.call_tool("get_cube_state", {})).structuredContent
            if st["status"] != "active":
                continue
            await asyncio.sleep(think_s)
            if cycle:                       # a fixed, hopeless policy (never solves)
                move = cycle[st["steps_used"] % len(cycle)]
            elif first_move:
                move, first_move = first_move, None
            else:
                move = solve_optimal(st["cube_facelets"])[0]
            res = (await s.call_tool("make_move", {"move": move})).structuredContent
            if res["status"] != "active":
                return res


def run_agent_thread(api_url, **kw):
    out = {}
    t = threading.Thread(target=lambda: out.update(result=asyncio.run(external_agent(api_url, **kw))), daemon=True)
    t.start()
    return t, out


class ManualAgentTests(unittest.TestCase):
    def test_manual_agent_solves_optimally_through_real_mcp_server(self):
        puzzle = make_puzzles("primary", 1, seed=11)[0]
        events = []
        with Backend() as be:
            t, out = run_agent_thread(be.url, think_s=0.2)
            trial = run_trial(ClaudeCodeMCPAdapter("claude-code", launch="manual"), puzzle, events.append)
            t.join(10)
        self.assertTrue(trial["solved"])
        self.assertEqual(trial["gap"], 0)
        self.assertEqual(trial["moves_applied"], puzzle["optimal"])
        self.assertEqual(out["result"]["status"], "solved")
        self.assertIsNone(trial["error"])
        # latency is the agent's own think time (0.2s), not startup or the 25s long-poll
        for s in trial["steps"]:
            self.assertGreaterEqual(s["latency_s"], 0.18)
            self.assertLess(s["latency_s"], 1.5)
        types = [e["type"] for e in events]
        self.assertEqual(types[0], "trial_start")
        self.assertEqual(types[-1], "trial_end")
        self.assertIn("agent_status", types)
        self.assertEqual(sum(e["type"] == "move" for e in events), len(trial["steps"]))

    def test_invalid_move_is_counted_not_converted(self):
        puzzle = make_puzzles("primary", 1, seed=12)[0]
        with Backend() as be:
            t, _ = run_agent_thread(be.url, first_move="r'")
            trial = run_trial(ClaudeCodeMCPAdapter("claude-code", launch="manual"), puzzle)
            t.join(10)
        self.assertTrue(trial["solved"])
        self.assertEqual(trial["malformed_steps"], 1)
        self.assertEqual(trial["steps"][0]["move"], None)
        self.assertEqual(trial["steps_used"], puzzle["optimal"] + 1)   # the bad move used up a step
        self.assertEqual(trial["moves_applied"], puzzle["optimal"])

    def test_move_limit_ends_the_episode(self):
        puzzle = make_puzzles("extension", 1, seed=13)[0]
        with Backend() as be:
            t, out = run_agent_thread(be.url, cycle=["U", "U'"])
            trial = run_trial(ClaudeCodeMCPAdapter("claude-code", launch="manual"), puzzle, max_moves=3)
            t.join(10)
        self.assertFalse(trial["solved"])
        self.assertEqual(trial["steps_used"], 3)
        self.assertEqual(out["result"]["status"], "limit_reached")

    def test_endpoints_when_no_game_is_active(self):
        import httpx
        with Backend() as be:
            self.assertIsNone(BRIDGE.active)
            r = httpx.get(f"{be.url}/api/agent/state").json()
            self.assertEqual(r["status"], "waiting")
            r = httpx.post(f"{be.url}/api/agent/move", json={"move": "R"}).json()
            self.assertEqual((r["status"], r["accepted"]), ("ended", False))


class AutoLaunchTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.fake = Path(self.tmp.name) / "claude"
        self.fake.write_text(f"#!{sys.executable}\n" + FAKE.read_text())
        self.fake.chmod(self.fake.stat().st_mode | stat.S_IEXEC)
        self.log = Path(self.tmp.name) / "argv.json"
        os.environ.update(FAKE_CLAUDE_ARGV_LOG=str(self.log), PROJECT_ROOT=str(ROOT))
        os.environ.pop("FAKE_CLAUDE_MODE", None)

    def tearDown(self):
        for k in ("FAKE_CLAUDE_ARGV_LOG", "PROJECT_ROOT", "FAKE_CLAUDE_MODE", "ANTHROPIC_API_KEY"):
            os.environ.pop(k, None)
        self.tmp.cleanup()

    def adapter(self, **kw):
        return ClaudeCodeMCPAdapter("claude-code", launch="auto", claude_bin=str(self.fake), **kw)

    def test_backend_launches_claude_with_the_mcp_config_and_it_solves(self):
        os.environ["ANTHROPIC_API_KEY"] = "sk-should-not-reach-claude"
        puzzle = make_puzzles("primary", 1, seed=14)[0]
        with Backend() as be:
            trial = run_trial(self.adapter(api_url=be.url, claude_model="opus"), puzzle)
        self.assertTrue(trial["solved"])
        self.assertEqual(trial["gap"], 0)
        seen = json.loads(self.log.read_text())
        argv = seen["argv"]
        self.assertEqual(argv[0], "-p")
        for flag in ("--mcp-config", "--strict-mcp-config", "--allowedTools", "--tools", "--model"):
            self.assertIn(flag, argv)
        self.assertEqual(argv[argv.index("--model") + 1], "opus")
        self.assertIn("mcp__cuberace__make_move", argv[argv.index("--allowedTools") + 1])
        self.assertNotEqual(Path(seen["cwd"]).resolve(), ROOT)       # runs outside the project
        self.assertFalse(seen["has_api_key"])                         # subscription, not API billing
        self.assertIsNone(BRIDGE.active)                              # session closed afterwards

    def test_effort_setting_is_passed_to_the_cli(self):
        puzzle = make_puzzles("primary", 1, seed=16)[0]
        with Backend() as be:
            trial = run_trial(self.adapter(api_url=be.url, effort="low"), puzzle)
        self.assertTrue(trial["solved"])
        argv = json.loads(self.log.read_text())["argv"]
        self.assertEqual(argv[argv.index("--effort") + 1], "low")
        with self.assertRaises(ValueError):
            self.adapter(effort="off")

    def test_claude_crash_marks_trial_failed_with_reason(self):
        os.environ["FAKE_CLAUDE_MODE"] = "exit"
        puzzle = make_puzzles("primary", 1, seed=15)[0]
        with Backend() as be:
            trial = run_trial(self.adapter(api_url=be.url), puzzle)
        self.assertFalse(trial["solved"])
        self.assertEqual(trial["steps_used"], 0)
        self.assertIn("code 3", trial["error"])
        self.assertIn("not logged in", trial["error"])
        self.assertIsNone(trial["avg_move_latency_s"])
        self.assertIsNone(BRIDGE.active)

    def test_preflight_fails_fast_when_cli_missing(self):
        missing = ClaudeCodeMCPAdapter("x", launch="auto", claude_bin="definitely-not-installed-claude")
        with self.assertRaises(RuntimeError) as cm:
            missing.preflight()
        self.assertIn("Claude Code CLI not found", str(cm.exception))
        ClaudeCodeMCPAdapter("x", launch="manual", claude_bin="nope").preflight()   # manual needs no CLI
        self.adapter().preflight()                                                  # fake binary exists


class DisabledModelTests(unittest.TestCase):
    def test_disabled_entries_are_skipped(self):
        from backend.adapters.registry import load_model_configs
        with tempfile.TemporaryDirectory() as d:
            f = Path(d) / "m.json"
            f.write_text(json.dumps({"models": [
                {"id": "a", "interface": "mock"},
                {"id": "b", "interface": "mock", "enabled": False},
                {"id": "c", "interface": "mock", "enabled": True}]}))
            self.assertEqual([m["id"] for m in load_model_configs(f)], ["a", "c"])


if __name__ == "__main__":
    unittest.main()
