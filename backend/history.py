"""Reading back recorded runs (results/NNNN/events.jsonl) for the run list and replay."""
import json
from datetime import datetime
from pathlib import Path


def run_dirs(root):
    root = Path(root)
    if not root.exists():
        return []
    return sorted((d for d in root.iterdir() if d.is_dir() and d.name.isdigit()),
                  key=lambda d: d.name, reverse=True)


def read_events(run_dir):
    """All events of a run, oldest first. A truncated last line (crash mid-write) is skipped."""
    path = Path(run_dir) / "events.jsonl"
    if not path.exists():
        return []
    events = []
    for line in path.read_text().splitlines():
        try:
            events.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return events


def trials_from_events(events, upto=None):
    """Rebuild trial records (with per-step latency) from the first `upto` finished trials.

    trial_end events omit step detail, so latencies come from the move events.
    """
    steps, trials = {}, []
    for e in events:
        if e["type"] == "move":
            steps.setdefault((e["model"], e["puzzle"]), []).append({"latency_s": e["latency_s"]})
        elif e["type"] == "trial_end":
            if upto is not None and len(trials) >= upto:
                break
            trials.append({**e["trial"], "steps": steps.get((e["model"], e["puzzle"]), [])})
    return trials


def _label(m):
    return m.get("label") or m["id"] if isinstance(m, dict) else str(m)


def describe_run(run_dir):
    """One-line description of a recorded run for the 'previous runs' list."""
    run_dir = Path(run_dir)
    events = read_events(run_dir)
    start = next((e for e in events if e["type"] == "run_start"), None)
    last = events[-1] if events else None
    if last and last["type"] == "run_end":
        status = last.get("status", "finished")
    elif last and last["type"] == "run_error":
        status = "error"
    else:
        status = "incomplete"
    created = start["ts"] if start and "ts" in start else datetime.fromtimestamp(run_dir.stat().st_mtime).isoformat()
    return {
        "run_id": run_dir.name,
        "set": start["set"] if start else None,
        "n_puzzles": len(start["puzzles"]) if start else 0,
        "models": [_label(m) for m in start["models"]] if start else [],
        "trials": sum(e["type"] == "trial_end" for e in events),
        "created": created,
        "status": status,
        "replayable": start is not None,
    }
