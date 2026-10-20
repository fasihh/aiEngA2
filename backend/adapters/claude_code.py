"""Claude Code (via MCP) as a benchmark player, using your Claude subscription.

Unlike the OpenRouter adapters this is an *agent*: instead of answering one question
per state it calls the cube MCP server's tools until the cube is solved or the move
limit is hit. `play()` runs one whole puzzle and reports each move as it happens.

launch="auto"   the backend starts `claude -p` itself with the cube MCP server attached
launch="manual" the backend waits for you to attach your own Claude Code session
                (see docs/MCP.md)
"""
import atexit
import json
import os
import queue
import secrets
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

from ..agent_bridge import BRIDGE, MoveRequest
from ..cube import MOVES
from .base import ABORTED, INSTRUCTIONS, MAX_RETRIES, RECENT_MOVES_N, TIMEOUT_S, Adapter, validate_move

MCP_SERVER = Path(__file__).resolve().parent.parent / "mcp_server.py"
SERVER_NAME = "cuberace"
TOOLS = [f"mcp__{SERVER_NAME}__get_cube_state", f"mcp__{SERVER_NAME}__make_move"]

STEP_TIMEOUT_S = TIMEOUT_S * (MAX_RETRIES + 1)   # same budget as one retried API step
CONNECT_TIMEOUT_AUTO_S = 180                     # CLI start-up and sign-in check
CONNECT_TIMEOUT_MANUAL_S = 900                   # time for you to attach a session

# Every Claude Code process we start, so none can outlive the program.
_LIVE = set()
_LIVE_LOCK = threading.Lock()
_abort = threading.Event()


def _signal_group(proc, sig):
    """Signal the process and everything it spawned (its MCP server). The child leads its own group."""
    try:
        os.killpg(proc.pid, sig)
    except (ProcessLookupError, PermissionError):
        pass


def kill_all_agents(abort=True):
    """Terminate every Claude Code process we started, with its MCP server. Returns how many.

    abort=True also makes any episode in progress end at once as ABORTED.
    """
    if abort:
        _abort.set()
    with _LIVE_LOCK:
        procs = list(_LIVE)
    for p in procs:
        _signal_group(p, signal.SIGTERM)
    deadline = time.monotonic() + 3
    for p in procs:
        try:
            p.wait(timeout=max(0.0, deadline - time.monotonic()))
        except subprocess.TimeoutExpired:
            _signal_group(p, signal.SIGKILL)
    return len(procs)


atexit.register(kill_all_agents)

PROMPT = (
    INSTRUCTIONS + "\n\n"
    "You have two tools: get_cube_state and make_move. Call get_cube_state first. Then make "
    "one move at a time with make_move, choosing the single move most likely to progress toward "
    "the solved cube, and keep going until make_move reports that the cube is solved or the move "
    "limit is reached. Do not use any other tools, write code, or ask questions."
)


class ClaudeCodeMCPAdapter(Adapter):
    is_agent = True

    def __init__(self, model_id, label=None, launch="auto", claude_model="opus", claude_bin=None,
                 api_url=None, extra_args=None, use_subscription=True, effort=None):
        super().__init__(model_id, label)
        if effort not in (None, "low", "medium", "high", "xhigh", "max"):
            raise ValueError("effort must be low, medium, high, xhigh or max (or omitted for the CLI default)")
        self.effort = effort
        if launch not in ("auto", "manual"):
            raise ValueError("launch must be 'auto' or 'manual'")
        self.launch = launch
        self.claude_model = claude_model
        self.claude_bin = claude_bin or os.environ.get("CLAUDE_BIN", "claude")
        self.api_url = api_url or os.environ.get("CUBE_API_URL", "http://127.0.0.1:8000")
        self.extra_args = list(extra_args or [])
        self.use_subscription = use_subscription

    def get_move(self, state, recent_moves):
        raise NotImplementedError("agent adapters use play()")

    def preflight(self):
        if self.launch == "auto" and shutil.which(self.claude_bin) is None:
            raise RuntimeError(
                f"Claude Code CLI not found ({self.claude_bin!r}). Install it and sign in, or set "
                "launch to 'manual' in models.json (see docs/MCP.md).")

    # -- process ---------------------------------------------------------------
    def _command(self, config_path):
        return [
            self.claude_bin, "-p", PROMPT,
            "--model", self.claude_model,
            "--mcp-config", str(config_path), "--strict-mcp-config",
            "--allowedTools", ",".join(TOOLS),
            "--tools", "",                       # no built-in tools: only the two cube tools
            *(["--effort", self.effort] if self.effort else []),
            *self.extra_args,
        ]

    def _spawn(self, token, workdir):
        config = {"mcpServers": {SERVER_NAME: {
            "command": sys.executable,
            "args": [str(MCP_SERVER)],
            "env": {"CUBE_API_URL": self.api_url, "CUBE_SESSION_TOKEN": token},
        }}}
        config_path = Path(workdir) / "mcp.json"
        config_path.write_text(json.dumps(config))
        env = dict(os.environ)
        if self.use_subscription:
            # An API key in the environment would make Claude Code bill the API instead of the plan.
            env.pop("ANTHROPIC_API_KEY", None)
        log = open(Path(workdir) / "claude.log", "w+")
        # Run in an empty directory so no project CLAUDE.md or settings leak into the agent.
        cmd = self._command(config_path)
        if shutil.which("setpriv"):
            # Linux: have the kernel stop Claude if this program is killed outright (SIGKILL, crash).
            cmd = ["setpriv", "--pdeathsig", "SIGTERM", *cmd]
        # Own session/process group so the whole tree can be killed together.
        proc = subprocess.Popen(cmd, cwd=workdir, env=env, stdin=subprocess.DEVNULL, stdout=log,
                                stderr=subprocess.STDOUT, start_new_session=True)
        with _LIVE_LOCK:
            _LIVE.add(proc)
        return proc, log

    @staticmethod
    def _tail(log, n=600):
        log.flush()
        log.seek(0)
        return log.read()[-n:].strip()

    # -- one puzzle ------------------------------------------------------------
    def play(self, game, max_moves, on_step, notify):
        """Run one puzzle. Returns (steps, applied_moves, error_or_None)."""
        _abort.clear()
        token = secrets.token_hex(8) if self.launch == "auto" else None
        session = BRIDGE.open(game.state, max_moves, token)
        steps, applied, error = [], [], None
        proc = log = None
        workdir = tempfile.mkdtemp(prefix="cuberace-")
        try:
            if self.launch == "auto":
                proc, log = self._spawn(token, workdir)
                notify("Claude Code started")
            else:
                notify("Waiting for you to attach Claude Code (see docs/MCP.md)")

            while len(steps) < max_moves:
                req, error = self._next_request(session, proc, log)
                if req is None:
                    break
                step = self._apply(session, game, req, applied, len(steps) + 1)
                steps.append(step)
                on_step(step)
                done = game.solved or len(steps) >= max_moves
                req.respond(self._reply(session, game, step, done, len(steps), max_moves), session)
                if done:
                    break
        finally:
            BRIDGE.close(session)
            if proc is not None:
                self._reap(proc)
                if error and error != ABORTED and log is not None:
                    error = f"{error}: {self._tail(log)}" if self._tail(log) else error
                log.close()
            shutil.rmtree(workdir, ignore_errors=True)
        return steps, applied, error

    def _next_request(self, session, proc, log):
        """Wait for the agent's next move; returns (request, None) or (None, error)."""
        while True:
            try:
                return session.inbox.get(timeout=0.25), None
            except queue.Empty:
                pass
            if _abort.is_set():
                return None, ABORTED
            if proc is not None and proc.poll() is not None:
                try:   # a move may have landed just before the process exited
                    return session.inbox.get_nowait(), None
                except queue.Empty:
                    return None, f"Claude Code exited (code {proc.returncode}) before the puzzle ended"
            now = time.perf_counter()
            if session.connected:
                # Measured from the last reply we sent, so polling get_cube_state cannot stall a step forever.
                limit, since, what = STEP_TIMEOUT_S, session.last_response_at or session.started_at, "no move"
            else:
                limit = CONNECT_TIMEOUT_AUTO_S if self.launch == "auto" else CONNECT_TIMEOUT_MANUAL_S
                since, what = session.started_at, "no connection"
            if now - since > limit:
                return None, f"timed out: {what} from the agent for {limit}s"

    @staticmethod
    def _apply(session, game, req: MoveRequest, applied, step_no):
        baseline = session.last_response_at if session.last_response_at is not None else session.started_at
        latency = max(0.0, req.arrival - baseline)
        move = validate_move(req.move)
        errors = []
        if move is not None:
            game.apply(move)
            applied.append(move)
            session.state, session.recent = game.state, applied[-RECENT_MOVES_N:]
        else:
            errors.append(f"invalid move: {req.move!r}")
        session.steps_used = step_no
        return {"step": step_no, "move": move, "valid": move is not None, "latency_s": latency,
                "confidence": None, "retries": 0, "errors": errors,
                "raw": str(req.move), "state_after": game.state}

    @staticmethod
    def _reply(session, game, step, done, used, max_moves):
        status = "solved" if game.solved else "limit_reached" if done else "active"
        reply = session.snapshot(status)
        reply["accepted"] = step["valid"]
        if not step["valid"]:
            reply["error"] = f"{step['raw']!r} is not one of the allowed moves: {' '.join(MOVES)}"
        reply["message"] = {
            "solved": "The cube is solved. You are done.",
            "limit_reached": "Move limit reached. You are done.",
        }.get(status, "Move applied. Make the next move." if step["valid"] else "Move rejected (it still used a move).")
        return reply

    @staticmethod
    def _reap(proc):
        try:
            proc.wait(timeout=20)   # the CLI normally exits on its own once told it is done
        except subprocess.TimeoutExpired:
            _signal_group(proc, signal.SIGTERM)
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                _signal_group(proc, signal.SIGKILL)
                proc.wait()
        _signal_group(proc, signal.SIGTERM)   # sweep anything it left behind (e.g. the MCP server)
        with _LIVE_LOCK:
            _LIVE.discard(proc)
