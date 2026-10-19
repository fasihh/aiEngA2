"""Exact shortest-solution search (half-turn metric) by meet-in-the-middle BFS.

A backward table of every state within BACK_DEPTH moves of solved is built once.
The forward search expands from the scrambled state layer by layer; after
expanding to depth f, every path of length <= f + BACK_DEPTH has been seen, so
the first time the best candidate is <= that bound it is provably optimal.
"""
from functools import lru_cache

from .cube import MOVES, SOLVED, apply_move, inverse_sequence

BACK_DEPTH = 3


@lru_cache(maxsize=1)
def _back_table():
    """state -> list of moves that takes that state to SOLVED (shortest)."""
    table = {SOLVED: []}
    frontier = [SOLVED]
    for _ in range(BACK_DEPTH):
        nxt = []
        for s in frontier:
            for m in MOVES:
                t = apply_move(s, m)
                if t not in table:
                    # s --m--> t, so from t the inverse of m leads back to s.
                    table[t] = [inverse_sequence([m])[0]] + table[s]
                    nxt.append(t)
        frontier = nxt
    return table


def solve_optimal(state, max_depth=8):
    """Return a shortest move list solving `state`, or None if deeper than max_depth."""
    back = _back_table()
    seen = {state: []}
    frontier = [state]
    best = None
    f = 0
    while True:
        for s in frontier:
            if s in back:
                cand = seen[s] + back[s]
                if best is None or len(cand) < len(best):
                    best = cand
        if best is not None and len(best) <= f + BACK_DEPTH:
            return best
        if f + BACK_DEPTH >= max_depth:
            return best if best is not None and len(best) <= max_depth else None
        nxt = []
        for s in frontier:
            for m in MOVES:
                t = apply_move(s, m)
                if t not in seen:
                    seen[t] = seen[s] + [m]
                    nxt.append(t)
        frontier = nxt
        f += 1


def optimal_distance(state, max_depth=8):
    sol = solve_optimal(state, max_depth)
    return None if sol is None else len(sol)
