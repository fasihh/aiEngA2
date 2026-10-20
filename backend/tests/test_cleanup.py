"""No Claude Code process may outlive the program: Stop, shutdown signals, normal exit."""
import json
import os
import shutil
import signal
import stat
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

import httpx

from backend.adapters import claude_code
from backend.adapters.base import ABORTED
from backend.adapters.claude_code import ClaudeCodeMCPAdapter, kill_all_agents
from backend.benchmark import make_puzzles, run_benchmark, run_trial
from backend.tests.test_agent import Backend, free_port

ROOT = Path(__file__).resolve().parents[2]
FAKE = Path(__file__).with_name("fake_claude.py")
SERVER = Path(__file__).with_name("serve_for_test.py")


def alive(pid):
    try:
        return Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()[0] != "Z"
    except (FileNotFoundError, ProcessLookupError):
        return False


def wait_until(cond, timeout=15):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if cond():
            return True
        time.sleep(0.1)
    return False


class CleanupBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.fake = self.dir / "claude"
        self.fake.write_text(f"#!{sys.executable}\n" + FAKE.read_text())
        self.fake.chmod(self.fake.stat().st_mode | stat.S_IEXEC)
        self.pids_file = self.dir / "pids.json"
        self.env = {"FAKE_CLAUDE_MODE": "hang", "FAKE_CLAUDE_PIDS": str(self.pids_file),
                    "FAKE_CLAUDE_ARGV_LOG": str(self.dir / "argv.json"), "PROJECT_ROOT": str(ROOT)}

    def tearDown(self):
        kill_all_agents()
        if self.pids_file.exists():   # the fake's dummy child is not a real MCP server, so reap it here
            try:
                os.kill(json.loads(self.pids_file.read_text())["grandchild"], signal.SIGKILL)
            except (ProcessLookupError, KeyError, ValueError):
                pass
        self.tmp.cleanup()

    def pids(self):
        assert wait_until(self.pids_file.exists), "fake claude never started"
        time.sleep(0.2)
        return json.loads(self.pids_file.read_text())


class StopTests(CleanupBase):
    def setUp(self):
        super().setUp()
        os.environ.update(self.env)

    def tearDown(self):
        for k in self.env:
            os.environ.pop(k, None)
        super().tearDown()

    def adapter(self, url):
        return ClaudeCodeMCPAdapter("cc", launch="auto", claude_bin=str(self.fake), api_url=url)

    def test_kill_all_agents_ends_trial_and_leaves_no_process(self):
        puzzle = make_puzzles("primary", 1, seed=31)[0]
        out = {}
        with Backend() as be:
            t = threading.Thread(target=lambda: out.update(trial=run_trial(self.adapter(be.url), puzzle)), daemon=True)
            t.start()
            pids = self.pids()
            self.assertTrue(alive(pids["claude"]) and alive(pids["grandchild"]))
            self.assertEqual(kill_all_agents(), 1)
            t.join(10)
        self.assertEqual(out["trial"]["error"], ABORTED)
        self.assertTrue(wait_until(lambda: not alive(pids["claude"]) and not alive(pids["grandchild"])),
                        "claude or its child survived")
        self.assertEqual(claude_code._LIVE, set())

    def test_stopped_trial_is_dropped_from_results(self):
        puzzles = make_puzzles("primary", 2, seed=32)
        out = {}
        with Backend() as be:
            t = threading.Thread(target=lambda: out.update(trials=run_benchmark([self.adapter(be.url)], puzzles)), daemon=True)
            t.start()
            self.pids()
            kill_all_agents()
            t.join(10)
        self.assertEqual(out["trials"], [])


class ShutdownSignalTests(CleanupBase):
    def run_case(self, sig, check_grandchild=True):
        port = free_port()
        (self.dir / "models.json").write_text(json.dumps({"models": [{
            "id": "cc", "label": "CC", "interface": "claude_code", "launch": "auto",
            "claude_bin": str(self.fake), "api_url": f"http://127.0.0.1:{port}"}]}))
        env = {**os.environ, **self.env, "MODELS": str(self.dir / "models.json"),
               "RESULTS": str(self.dir / "results"), "PORT": str(port)}
        server = subprocess.Popen([sys.executable, str(SERVER)], env=env, cwd=self.dir)
        try:
            def up():
                try:
                    return server.poll() is None and httpx.get(f"http://127.0.0.1:{port}/api/sets", timeout=1).status_code == 200
                except httpx.HTTPError:
                    return False
            self.assertTrue(wait_until(up, 20), "server did not start")
            r = httpx.post(f"http://127.0.0.1:{port}/api/benchmark/start", json={"set": "warmup", "n": 1})
            self.assertEqual(r.status_code, 200, r.text)
            pids = self.pids()
            self.assertTrue(alive(pids["claude"]) and alive(pids["grandchild"]))
            server.send_signal(sig)
            self.assertTrue(wait_until(lambda: server.poll() is not None, 20), f"server ignored {sig.name}")
            self.assertTrue(wait_until(lambda: not alive(pids["claude"])), f"claude survived {sig.name}")
            if check_grandchild:   # the MCP server normally also exits on its own when Claude's pipe closes
                self.assertTrue(wait_until(lambda: not alive(pids["grandchild"])),
                                f"claude's MCP-server child survived {sig.name}")
        finally:
            if server.poll() is None:
                server.kill()
                server.wait()

    def test_sigterm(self):
        self.run_case(signal.SIGTERM)

    def test_sigint_ctrl_c(self):
        self.run_case(signal.SIGINT)

    def test_sighup_terminal_closed(self):
        self.run_case(signal.SIGHUP)

    @unittest.skipUnless(shutil.which("setpriv"), "needs util-linux setpriv")
    def test_sigkill_server_still_stops_claude(self):
        """kill -9 gives the server no chance to clean up; the kernel stops Claude instead."""
        self.run_case(signal.SIGKILL, check_grandchild=False)


if __name__ == "__main__":
    unittest.main()
