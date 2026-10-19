# Rubik's Cube Race: Implementation Plan

Benchmark four models (3 Decisions-API models + Claude Opus 5.5) solving the same scrambled 3×3 cube via OpenRouter, measuring speed and solution quality.

**Stack:** Python + FastAPI backend, plain HTML/JS frontend (or React), `httpx` for OpenRouter.

## Budget note: no OpenRouter credit yet
- Model list lives in a config file (`backend/models.json`): each entry has `id`, `label`, `interface` (`decisions` | `chat` | `mock`) and `category`. Swapping stand-ins for the real four models is a config change only.
- Stand-ins for development: OpenRouter `:free` chat models (e.g. `nvidia/nemotron-3-super-120b-a12b:free`, `google/gemma-4-31b-it:free`) through the chat adapter. Free tiers have tight rate limits and daily caps, so run in small batches.
- Offline `mock` adapters (random mover, greedy heuristic, BFS-oracle) with simulated latency and error rates let the whole pipeline and dashboard be built and tested for free.
- None of the four assignment models is free. Top up a few dollars for the final run and note in the README which models were actually used.

## Phase 0: Setup
- Repo layout: `backend/` (`cube.py`, `solver.py`, `adapters/`, `models.json`, `benchmark.py`, `main.py`), `frontend/`, `results/`, `.env.example`, `.gitignore` (with `.env`), `README.md`.
- Check OpenRouter docs for the Decisions API schema and the four model IDs (needs a key, so do it once credit is available). The Decisions API is alpha, so its response format is the biggest unknown.

## Phase 1: Cube engine (`cube.py`)
- Use a 54-facelet state or an existing library such as `pycuber`.
- One move set for all models: the 18 half-turn-metric moves (`U U' U2 …`).
- Provide `apply(state, move)`, `is_solved(state)`, `scramble(n, seed)`, `to_facelets()`.
- Avoid trivial scrambles by never repeating a face on consecutive turns.
- Seeded scrambles so every model gets the same puzzle and runs are reproducible.
- Tests: a move followed by its inverse restores the cube; four quarter turns equal the identity; a scramble followed by its reverse is solved.

## Phase 2: Optimal solver (`solver.py`)
- BFS or bidirectional BFS from the scrambled state to solved, with a visited set on compact state tuples.
- Depth 5 over 18 moves is at most about 2M nodes, which is fine. Bidirectional search is much faster.
- Compute distance from the actual state, not the scramble length, because scrambles can contain redundant moves. Store `optimal` with each puzzle.
- For the 15–20 turn extension, BFS is infeasible. Report optimality gap only for the primary set, or use `kociemba` as a near-optimal reference and label it as such.

## Phase 3: Model adapters (`adapters/`)
- One interface: `get_move(state, recent_moves) -> {move, latency, raw, valid, confidence?, retries}`.
- **Mock adapter:** same interface, no network; random / greedy / BFS-oracle strategies with configurable latency and malformed-output rate.
- **Decisions adapter:** request as in the assignment PDF: `state.cube_facelets`, `recent_moves`, and a `choice` question with all 18 moves in `criteria`. Parse the selected option and any probability/confidence field. Shared prompt text; only the model ID changes.
- **Opus adapter:** chat completions with a system prompt, the state, allowed moves and recent moves. Use `response_format` as a JSON schema with an enum of the 18 moves.
- **Shared policy:** same timeout (e.g. 60s) and one retry for every model; log every retry and invalid response.
- **Invalid moves:** count as malformed and never apply them. Use one policy for all models (e.g. retry once, then count the move as wasted). Wasted attempts count toward the 30-move cap.
- Time only the request, with `time.perf_counter()`.

## Phase 4: Benchmark runner (`benchmark.py`)
- Generate N seeded puzzles per set; loop over puzzles and models.
- Per trial: loop until solved or 30 moves, calling the adapter and applying the move. Record per-move latency, total wall time, move list, solved flag, malformed count and optimum.
- Sequential runs give the fairest timing; parallel across models is faster for the demo. Pick one and state it in the README.
- Save results to JSON/CSV in `results/` so the dashboard can be rebuilt without re-running.

## Phase 5: FastAPI API
- `POST /benchmark/start` launches a background run.
- `GET /benchmark/stream` (SSE) streams per-move events (model, move, state, latency) for the live UI.
- `GET /results` returns aggregated stats.
- API key read from the environment only; never sent to the frontend.

## Phase 6: Frontend
- Cube view (3D or 2D net) and a "Run Benchmark" button.
- Four lanes, one per model: current move, move history, Solved / Move limit reached badge.
- Stats dashboard (table and charts): average latency per move, total time, move count, success rate, optimality gap.
- Fixed header with **student name and roll number**, visible for the whole recording.

## Phase 7: Run and analyze
1. Warm-up (3–4 turns, 2 puzzles) to debug adapters.
2. Primary (4–5 turns, 10 puzzles) for the main data (minimum 5).
3. Extension (15–20 turns, a few puzzles) for failure analysis.
4. Aggregate, then answer the five required questions with numbers from the runs:
   1. Which model is fastest at making decisions?
   2. Which achieved the highest solve rate?
   3. Which found the shortest solutions, and how close to optimal?
   4. Did the fastest model also have the best solution quality?
   5. Would you use the fast decision model, the frontier LLM, or a hybrid in a real application, and what evidence supports that?

## Phase 8: Submission
- README with setup, env vars, how to run.
- Screen recording, which must include all four of:
  1. In-progress stage: models playing.
  2. Completion stage: models finished.
  3. A final slide with the benchmark statistics.
  4. Name and roll number visible on screen for the entire demo.
- Push to GitHub with no keys committed.

## Risks
- Decisions API is alpha and model IDs may differ from the PDF; verify first.
- Models may cycle or play badly; the 30-move cap and malformed-response handling must be clear.
- Opus is expensive and rate limits apply; test on warm-up first.
- Keep a saved results file as a fallback for the live demo.
