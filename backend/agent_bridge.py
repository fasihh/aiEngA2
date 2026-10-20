"""Hand-off between an external agent (Claude Code via MCP) and the benchmark runner.

The MCP server process calls the HTTP endpoints in main.py; those talk to this
in-process bridge. The runner stays authoritative: a submitted move blocks until
the runner has validated and applied it, and the reply carries the new state.

Timing: a step's latency is the time from the last tool response sent to the agent
(the first get_cube_state, then every make_move) to the next make_move request
arriving. That is the agent's own thinking/generation time plus a few
milliseconds of localhost and stdio overhead; CLI start-up is excluded.
"""
import queue
import threading
import time

from .cube import MOVES


class MoveRequest:
    def __init__(self, move):
        self.move = move
        self.arrival = time.perf_counter()
        self.done = threading.Event()
        self.reply = None

    def respond(self, reply, session):
        """Answer the waiting agent; its next step's clock starts now."""
        self.reply = reply
        session.last_response_at = time.perf_counter()
        session.touch()
        self.done.set()


class AgentSession:
    def __init__(self, state, max_moves, token=None):
        self.token = token                  # None = a manually attached agent
        self.state = state
        self.recent = []
        self.max_moves = max_moves
        self.steps_used = 0
        self.inbox = queue.Queue()
        self.started_at = time.perf_counter()
        self.last_response_at = None
        self.last_activity = self.started_at
        self.connected = False
        self.closed = False

    def touch(self):
        self.last_activity = time.perf_counter()

    def snapshot(self, status="active"):
        return {
            "status": status,
            "cube_facelets": self.state,
            "recent_moves": list(self.recent),
            "steps_used": self.steps_used,
            "steps_remaining": self.max_moves - self.steps_used,
            "max_moves": self.max_moves,
            "allowed_moves": MOVES,
        }

    def mark_state_read(self):
        """First get_cube_state starts the clock for the first move."""
        self.connected = True
        self.touch()
        if self.last_response_at is None:
            self.last_response_at = time.perf_counter()


class Bridge:
    """Holds the one active session (trials run sequentially)."""

    def __init__(self):
        self._cond = threading.Condition()
        self.active = None

    def open(self, state, max_moves, token=None):
        with self._cond:
            if self.active is not None and not self.active.closed:
                raise RuntimeError("an agent session is already active")
            self.active = AgentSession(state, max_moves, token)
            self._cond.notify_all()
            return self.active

    def close(self, session):
        """End the session and release any agent still waiting on a reply."""
        with self._cond:
            session.closed = True
            if self.active is session:
                self.active = None
            self._cond.notify_all()
        while True:
            try:
                req = session.inbox.get_nowait()
            except queue.Empty:
                break
            req.respond({"status": "ended", "accepted": False,
                         "message": "This game has ended."}, session)

    def find(self, token=None, wait_s=0.0):
        """The active session this caller may use, waiting up to wait_s for one to open.

        A caller presenting a token only sees the session that owns that token;
        a caller with no token only sees a manually attached session.
        """
        deadline = time.monotonic() + wait_s
        with self._cond:
            while True:
                s = self.active
                if s is not None and not s.closed and s.token == (token or None):
                    return s
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return None
                self._cond.wait(remaining)


BRIDGE = Bridge()
