"""Runs one benchmark at a time in a background thread and keeps its events for streaming."""
import threading
from datetime import datetime

from .adapters.claude_code import kill_all_agents
from .adapters.registry import MODELS_FILE, build_adapters
from .benchmark import MAX_MOVES, EventLog, make_puzzles, new_run_dir, save_results, summarize, run_benchmark


class Run:
    def __init__(self, run_id, run_dir, set_name, n, seed):
        self.id, self.dir = run_id, run_dir
        self.set, self.n, self.seed = set_name, n, seed
        self.events = []          # every event, in order, for replay by late/reconnecting clients
        self.status = "running"   # running | finished | stopped | error
        self.stop_flag = threading.Event()
        self._lock = threading.Lock()

    def add(self, event):
        with self._lock:
            self.events.append(event)

    def since(self, index):
        with self._lock:
            return self.events[index:]

    def info(self):
        return {"run_id": self.id, "status": self.status, "set": self.set, "n": self.n, "seed": self.seed}


class RunManager:
    def __init__(self, models_path=MODELS_FILE, results_root=None):
        self.models_path = models_path
        self.results_root = results_root
        self.runs = {}
        self.current = None
        self._lock = threading.Lock()

    def start(self, set_name, n, seed):
        """Start a run, or raise RuntimeError if one is already going."""
        with self._lock:
            if self.current and self.current.status == "running":
                raise RuntimeError("a benchmark is already running")
            adapters = build_adapters(self.models_path)  # fails fast (e.g. missing API key)
            for a in adapters:
                a.preflight()                            # e.g. Claude Code CLI not installed
            run_dir = new_run_dir(self.results_root)
            run = Run(run_dir.name, run_dir, set_name, n, seed)
            self.runs[run.id] = run
            self.current = run
        threading.Thread(target=self._work, args=(run, adapters), daemon=True).start()
        return run

    def stop(self):
        if self.current and self.current.status == "running":
            self.current.stop_flag.set()
            kill_all_agents()   # end an in-progress Claude Code puzzle now, not at the next trial
            return True
        return False

    def _work(self, run, adapters):
        log = EventLog(run.dir / "events.jsonl")

        def emit(event):
            log(event)
            run.add({"ts": datetime.now().isoformat(timespec="milliseconds"), **event})

        try:
            puzzles = make_puzzles(run.set, run.n, run.seed)
            emit({"type": "run_start", "run_id": run.id, "set": run.set, "puzzles": puzzles,
                  "models": [{"id": a.model_id, "label": a.label} for a in adapters],
                  "max_moves": MAX_MOVES})
            trials = run_benchmark(adapters, puzzles, emit, run.stop_flag.is_set)
            save_results(run.set, puzzles, trials, run.dir / "results.json")
            run.status = "stopped" if run.stop_flag.is_set() else "finished"
            emit({"type": "run_end", "status": run.status, "summary": summarize(trials) if trials else {}})
        except Exception as e:  # noqa: BLE001 - surface any failure to the stream
            run.status = "error"
            emit({"type": "run_error", "error": f"{type(e).__name__}: {e}"})
        finally:
            log.close()
