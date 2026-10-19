"""Offline adapters for developing the pipeline without API credit.

Strategies: random mover, greedy one-step heuristic, and a BFS oracle (plays the
exact shortest solution). Latency and malformed-output rate are configurable and
go through the same retry policy as the real adapters.
"""
import random
import time

from ..cube import MOVES, SOLVED, apply_move
from ..engine import reference_solution
from ..solver import solve_optimal
from .base import MAX_RETRIES, Adapter, MoveResult, validate_move


def _misplaced(state):
    return sum(a != b for a, b in zip(state, SOLVED))


class MockAdapter(Adapter):
    def __init__(self, model_id, strategy="random", latency_s=0.0, malformed_rate=0.0,
                 seed=None, label=None):
        super().__init__(model_id, label)
        self.strategy = strategy
        self.latency_s = latency_s
        self.malformed_rate = malformed_rate
        self.rng = random.Random(seed)

    def _choose(self, state, recent_moves):
        if self.strategy == "random":
            return self.rng.choice(MOVES)
        if self.strategy == "greedy":
            scored = [(_misplaced(apply_move(state, m)), self.rng.random(), m) for m in MOVES
                      if not recent_moves or m[0] != recent_moves[-1][0]]
            return min(scored)[2]
        if self.strategy == "oracle":
            sol = solve_optimal(state, max_depth=8) or reference_solution(state)
            return sol[0]
        raise ValueError(f"unknown strategy {self.strategy!r}")

    def get_move(self, state, recent_moves):
        t0 = time.perf_counter()
        errors, raw, retries = [], "", 0
        for attempt in range(MAX_RETRIES + 1):
            if self.latency_s:
                time.sleep(self.latency_s * self.rng.uniform(0.7, 1.3))
            move = self._choose(state, recent_moves)
            if self.rng.random() < self.malformed_rate:
                move = move.lower() + "?"  # simulated garbage
            raw = move
            if validate_move(move):
                return MoveResult(move, time.perf_counter() - t0, raw, True, None, retries, errors)
            errors.append(f"invalid move: {move!r}")
            if attempt < MAX_RETRIES:
                retries += 1
        return MoveResult(None, time.perf_counter() - t0, raw, False, None, retries, errors)
