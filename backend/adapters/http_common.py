"""Retry/timeout policy shared by the real (HTTP) adapters."""
import time

import httpx

from .base import MAX_RETRIES, RETRY_WITHOUT_HISTORY, TIMEOUT_S, Adapter, MoveResult, validate_move

OPENROUTER_BASE = "https://openrouter.ai/api"


class HttpAdapter(Adapter):
    """Subclasses implement `_attempt(state, recent_moves) -> (raw, move, confidence)`.

    `_attempt` may raise; any exception, timeout, HTTP error or invalid move
    consumes one try. The step ends after MAX_RETRIES retries.
    """

    def __init__(self, model_id, api_key, label=None, client=None):
        super().__init__(model_id, label)
        self.api_key = api_key
        self.client = client or httpx.Client(timeout=TIMEOUT_S)

    def _headers(self):
        return {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}

    def _attempt(self, state, recent_moves):
        raise NotImplementedError

    @staticmethod
    def _body_hint(exc):
        """The server's own error text for an HTTP failure; the bare status line rarely says why."""
        response = getattr(exc, "response", None)
        if response is None:
            return ""
        try:
            return " | server said: " + response.text.strip().replace("\n", " ")[:300]
        except Exception:  # noqa: BLE001 - e.g. a streamed body that cannot be read
            return ""

    def get_move(self, state, recent_moves):
        errors, raw, retries = [], "", 0
        t0 = time.perf_counter()
        history = recent_moves
        for attempt in range(MAX_RETRIES + 1):
            try:
                raw, move, conf = self._attempt(state, history)
                if validate_move(move):
                    return MoveResult(move, time.perf_counter() - t0, raw, True, conf, retries, errors)
                errors.append(f"invalid move: {move!r}")
            except Exception as e:  # noqa: BLE001 - every failure is logged and counted
                errors.append(f"{type(e).__name__}: {e}{self._body_hint(e)}")
            if attempt < MAX_RETRIES:
                retries += 1
                if RETRY_WITHOUT_HISTORY and history:
                    history = []
                    errors.append("retrying without move history")
        # "unavailable" = no attempt produced an answer at all (as opposed to a wrong answer)
        unavailable = not any(e.startswith("invalid move") for e in errors)
        return MoveResult(None, time.perf_counter() - t0, raw, False, None, retries, errors, unavailable)
