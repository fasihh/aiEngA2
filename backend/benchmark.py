"""Benchmark runner: seeded puzzles, one trial per (puzzle, model), aggregated stats.

Every model plays the same puzzles with the same 30-move cap, timeout and retry
policy. Trials run sequentially, so latencies are not distorted by concurrency.

CLI:  python -m backend.benchmark --set primary --n 5 [--seed 100] [--models path.json]
"""
import argparse
import json
import random
import statistics
import time
from datetime import datetime
from pathlib import Path

from .adapters.base import RECENT_MOVES_N
from .adapters.registry import MODELS_FILE, build_adapters
from .cube import SOLVED
from .engine import CubeGame, reference_solution
from .solver import solve_optimal

MAX_MOVES = 30
RESULTS_DIR = Path(__file__).resolve().parent.parent / "results"

# set name -> inclusive scramble-length range
SETS = {"warmup": (3, 4), "primary": (4, 5), "extension": (15, 20)}


def make_puzzles(set_name, n, seed=0):
    """n reproducible puzzles; exact optimum for short ones, Kociemba reference otherwise."""
    lo, hi = SETS[set_name]
    rng = random.Random(f"{set_name}-{seed}")
    puzzles = []
    while len(puzzles) < n:
        length = rng.randint(lo, hi)
        pseed = rng.randrange(2**31)
        game = CubeGame(length, pseed)
        state = game.initial_state
        if state == SOLVED:
            continue
        exact = solve_optimal(state, max_depth=8) if hi <= 8 else None
        optimal = len(exact) if exact is not None else len(reference_solution(state))
        puzzles.append({
            "id": f"{set_name}-{len(puzzles) + 1}",
            "set": set_name,
            "scramble_len": length,
            "seed": pseed,
            "scramble": game.scramble,
            "initial_state": state,
            "optimal": optimal,
            "optimal_is_exact": exact is not None,
        })
    return puzzles


def run_trial(adapter, puzzle, on_event=None, max_moves=MAX_MOVES):
    emit = on_event or (lambda e: None)
    game = CubeGame(0)
    game.cube.rotate(" ".join(puzzle["scramble"]))  # same scramble as every other model
    assert game.state == puzzle["initial_state"]
    base = {"model": adapter.model_id, "puzzle": puzzle["id"]}
    emit({**base, "type": "trial_start", "state": game.state, "optimal": puzzle["optimal"]})

    steps, applied = [], []
    t0 = time.perf_counter()
    for i in range(1, max_moves + 1):
        res = adapter.get_move(game.state, applied[-RECENT_MOVES_N:])
        if res.move is not None:
            game.apply(res.move)
            applied.append(res.move)
        step = {"step": i, "move": res.move, "valid": res.valid, "latency_s": res.latency_s,
                "confidence": res.confidence, "retries": res.retries, "errors": res.errors,
                "raw": res.raw, "state_after": game.state}
        steps.append(step)
        emit({**base, "type": "move", **step, "solved": game.solved})
        if game.solved:
            break
    total = time.perf_counter() - t0

    solved = game.solved
    exact = puzzle["optimal_is_exact"]
    trial = {
        "model": adapter.model_id,
        "label": adapter.label,
        "puzzle": puzzle["id"],
        "set": puzzle["set"],
        "solved": solved,
        "hit_move_limit": not solved,
        "moves_applied": len(applied),
        "steps_used": len(steps),
        "malformed_steps": sum(not s["valid"] for s in steps),
        "retries": sum(s["retries"] for s in steps),
        "avg_move_latency_s": statistics.fmean(s["latency_s"] for s in steps),
        "total_time_s": total,
        "optimal": puzzle["optimal"],
        "optimal_is_exact": exact,
        "gap": len(applied) - puzzle["optimal"] if solved and exact else None,
        "moves": applied,
        "steps": steps,
    }
    emit({**base, "type": "trial_end", "trial": {k: v for k, v in trial.items() if k != "steps"}})
    return trial


def summarize(trials):
    """Per-model aggregate stats (the dashboard table)."""
    out = {}
    for model in dict.fromkeys(t["model"] for t in trials):
        ts = [t for t in trials if t["model"] == model]
        solved = [t for t in ts if t["solved"]]
        gaps = [t["gap"] for t in ts if t["gap"] is not None]
        lat = [s["latency_s"] for t in ts for s in t["steps"]]
        out[model] = {
            "label": ts[0]["label"],
            "trials": len(ts),
            "solved": len(solved),
            "success_rate_pct": 100 * len(solved) / len(ts),
            "avg_move_latency_s": statistics.fmean(lat),
            "avg_total_time_s": statistics.fmean(t["total_time_s"] for t in ts),
            "avg_moves": statistics.fmean(t["moves_applied"] for t in ts),
            "avg_moves_solved": statistics.fmean(t["moves_applied"] for t in solved) if solved else None,
            "avg_optimality_gap": statistics.fmean(gaps) if gaps else None,
            "optimal_solves": sum(g == 0 for g in gaps),
            "malformed_steps": sum(t["malformed_steps"] for t in ts),
            "retries": sum(t["retries"] for t in ts),
        }
    return out


def run_benchmark(adapters, puzzles, on_event=None, should_stop=None):
    """Run every adapter on every puzzle; `should_stop()` is polled between trials."""
    trials = []
    for puzzle in puzzles:
        for adapter in adapters:
            if should_stop and should_stop():
                return trials
            trials.append(run_trial(adapter, puzzle, on_event))
    return trials


def new_run_dir(root=None):
    """Create and return the next numbered run folder: results/0001, results/0002, ..."""
    root = Path(root) if root else RESULTS_DIR
    root.mkdir(parents=True, exist_ok=True)
    nums = [int(d.name) for d in root.iterdir() if d.is_dir() and d.name.isdigit()]
    n = max(nums, default=0) + 1
    while True:
        try:
            run_dir = root / f"{n:04d}"
            run_dir.mkdir()  # fails if it already exists, so concurrent runs never share a folder
            return run_dir
        except FileExistsError:
            n += 1


class EventLog:
    """Append-only JSONL log: one flushed line per event, so a crash loses nothing."""

    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(exist_ok=True)
        self._f = self.path.open("a", buffering=1)

    def __call__(self, event):
        self._f.write(json.dumps({"ts": datetime.now().isoformat(timespec="milliseconds"), **event}) + "\n")
        self._f.flush()

    def close(self):
        self._f.close()


def save_results(set_name, puzzles, trials, path=None):
    path = Path(path) if path else new_run_dir() / "results.json"
    path.write_text(json.dumps({
        "set": set_name, "max_moves": MAX_MOVES, "puzzles": puzzles,
        "trials": trials, "summary": summarize(trials),
    }, indent=2))
    return path


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--set", choices=SETS, default="warmup")
    ap.add_argument("--n", type=int, default=5, help="number of puzzles")
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--models", default=str(MODELS_FILE))
    args = ap.parse_args()

    adapters = build_adapters(args.models)
    puzzles = make_puzzles(args.set, args.n, args.seed)

    def log(e):
        if e["type"] == "move":
            print(f"  {e['model']:<28} step {e['step']:>2} {str(e['move']):<3} {e['latency_s']:.2f}s")
        elif e["type"] == "trial_start":
            print(f"{e['puzzle']} / {e['model']} (optimal {e['optimal']})")
        elif e["type"] == "trial_end":
            t = e["trial"]
            print(f"  -> {'SOLVED' if t['solved'] else 'LIMIT'} in {t['moves_applied']} moves, gap {t['gap']}")

    run_dir = new_run_dir()
    events = EventLog(run_dir / "events.jsonl")
    events({"type": "run_start", "set": args.set, "puzzles": puzzles,
            "models": [a.model_id for a in adapters], "max_moves": MAX_MOVES})
    try:
        trials = run_benchmark(adapters, puzzles, lambda e: (events(e), log(e)))
    finally:
        events.close()
    path = save_results(args.set, puzzles, trials, run_dir / "results.json")
    print(f"\nsaved {path}\nevent log {events.path}")
    for m, s in summarize(trials).items():
        print(f"{s['label']:<20} success {s['success_rate_pct']:.0f}%  latency {s['avg_move_latency_s']:.2f}s  "
              f"moves {s['avg_moves']:.1f}  gap {s['avg_optimality_gap']}")


if __name__ == "__main__":
    main()
