"""FastAPI app. Run with:  uv run uvicorn backend.main:app --reload

The OpenRouter key is read from the OPENROUTER_API_KEY environment variable (or .env)
on the server only; it is never sent to the browser.
"""
import asyncio
import json
import os
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, Header, HTTPException, Query
from fastapi.responses import StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .adapters.registry import MODELS_FILE, load_model_configs
from .benchmark import RESULTS_DIR, SETS, summarize
from .service import RunManager

load_dotenv()

FRONTEND_DIR = Path(__file__).resolve().parent.parent / "frontend"
TERMINAL = {"run_end", "run_error"}


class StartRequest(BaseModel):
    set: str = "primary"
    n: int = Field(5, ge=1, le=50)
    seed: int = 0


def create_app(models_path=MODELS_FILE, results_root=RESULTS_DIR):
    app = FastAPI(title="Rubik's Cube Race")
    mgr = RunManager(models_path, results_root)
    results_root = Path(results_root)

    @app.get("/api/student")
    def student():
        """Name and roll number shown in the UI header (from STUDENT_NAME / STUDENT_ROLL_NO)."""
        return {"name": os.environ.get("STUDENT_NAME") or "SET STUDENT_NAME IN .env",
                "roll": os.environ.get("STUDENT_ROLL_NO") or "SET STUDENT_ROLL_NO IN .env"}

    @app.get("/api/models")
    def models():
        return [{k: v for k, v in m.items() if not k.startswith("_")} for m in load_model_configs(models_path)]

    @app.get("/api/sets")
    def sets():
        return {k: {"min_turns": lo, "max_turns": hi} for k, (lo, hi) in SETS.items()}

    @app.post("/api/benchmark/start")
    def start(req: StartRequest):
        if req.set not in SETS:
            raise HTTPException(422, f"set must be one of {list(SETS)}")
        try:
            return mgr.start(req.set, req.n, req.seed).info()
        except RuntimeError as e:
            raise HTTPException(409 if "already" in str(e) else 500, str(e)) from e

    @app.post("/api/benchmark/stop")
    def stop():
        return {"stopping": mgr.stop()}

    @app.get("/api/benchmark/status")
    def status():
        return mgr.current.info() if mgr.current else {"status": "idle"}

    @app.get("/api/benchmark/stream")
    async def stream(run_id: str | None = Query(None), last_event_id: str | None = Header(None)):
        """Server-sent events: replays the run from the start (or Last-Event-ID), then goes live."""
        run = mgr.runs.get(run_id) if run_id else mgr.current
        if run is None:
            raise HTTPException(404, "no such run")
        index = int(last_event_id) + 1 if last_event_id and last_event_id.isdigit() else 0

        async def gen():
            nonlocal index
            idle = 0.0
            while True:
                batch = run.since(index)
                for ev in batch:
                    yield f"id: {index}\nevent: {ev['type']}\ndata: {json.dumps(ev)}\n\n"
                    index += 1
                    if ev["type"] in TERMINAL:
                        return
                if batch:
                    idle = 0.0
                else:
                    await asyncio.sleep(0.05)
                    idle += 0.05
                    if idle >= 15:
                        yield ": keep-alive\n\n"
                        idle = 0.0

        return StreamingResponse(gen(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    @app.get("/api/runs")
    def runs():
        return sorted((d.name for d in results_root.iterdir() if d.is_dir() and d.name.isdigit()), reverse=True) \
            if results_root.exists() else []

    @app.get("/api/runs/{run_id}")
    def run_results(run_id: str):
        path = results_root / run_id / "results.json"
        if not run_id.isdigit() or not path.exists():
            raise HTTPException(404, "no saved results for that run")
        return json.loads(path.read_text())

    @app.get("/api/runs/{run_id}/live-summary")
    def live_summary(run_id: str):
        """Stats over the trials finished so far (works while the run is in progress)."""
        run = mgr.runs.get(run_id)
        if run is None:
            raise HTTPException(404, "no such run")
        # trial_end events omit step detail; rebuild per-step latency from the move events.
        steps = {}
        for e in run.since(0):
            if e["type"] == "move":
                steps.setdefault((e["model"], e["puzzle"]), []).append({"latency_s": e["latency_s"]})
        trials = [{**e["trial"], "steps": steps.get((e["model"], e["puzzle"]), [])}
                  for e in run.since(0) if e["type"] == "trial_end"]
        return {"run": run.info(), "summary": summarize(trials) if trials else {}}

    if FRONTEND_DIR.exists():
        app.mount("/", StaticFiles(directory=FRONTEND_DIR, html=True), name="frontend")
    return app


app = create_app()
