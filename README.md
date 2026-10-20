# Rubik's Cube Race

**AI Engineering, Assignment 02** · Fasih Hasan Khan · K230018

## Models compared

| Slot | Model | Category | Interface |
|---|---|---|---|
| 1 | `typesafe/jev-1.13` | System-1 decision model | OpenRouter Decisions API |
| 2 | `openai/gpt-6-luna-decisions` | System-1 decision model | OpenRouter Decisions API |
| 3 | `cloudflare/clef` | System-1 decision model | OpenRouter Decisions API |
| 4 | Claude Opus (the `opus` alias) | Frontier LLM | Claude Code with an MCP cube server |

The first three models share one request format and one question wording, and only the model identifier changes.

The assignment asks that Opus be tested through OpenRouter chat completions.
Here Claude Opus plays through **Claude Code**, using a Claude subscription instead of per-token billing: the cube is
exposed to it as an **MCP server**, and the backend launches Claude Code itself. The OpenRouter chat-completions
adapter is still included (`interface: "chat"`) and replaces the Claude Code entry by swapping one line in
`backend/models.json`. See [Claude Code and MCP setup](#claude-code-and-mcp-setup).

## Setup

Requirements: Python 3.12+, [uv](https://docs.astral.sh/uv/), and the Claude Code CLI signed in with a Claude
account (only for the Claude slot).

```bash
uv sync                       # install dependencies into .venv
cp .env.example .env          # then fill in the values below
uv run uvicorn backend.main:app
```

Open <http://127.0.0.1:8000>, choose a test set and a number of puzzles, and press **Run benchmark**.

### Environment variables (`.env.example` is the template)

| Variable | Purpose |
|---|---|
| `OPENROUTER_API_KEY` | OpenRouter key for the three Decisions models. Read by the backend only; never sent to the browser. |
| `STUDENT_NAME` | Shown in the page header throughout. |
| `STUDENT_ROLL_NO` | Shown in the page header throughout. |

### Choosing the models (`backend/models.json`)

Each entry has `id`, `label`, `category` and an `interface`:

| `interface` | Meaning | Extra fields |
|---|---|---|
| `decisions` | OpenRouter Decisions API | none |
| `chat` | OpenRouter chat completions, reply constrained to a JSON schema | `structured_output` |
| `claude_code` | Claude Code playing through MCP | `claude_model`, `effort`, `api_url`, `launch` |
| `mock` | Offline stand-in for development | `strategy`, `latency_s`, `malformed_rate`, `seed` |

`"enabled": false` removes an entry. The configuration used for the results below:

```json
{"models": [
  {"id": "typesafe/jev-1.13",            "label": "Jev 1.13",             "interface": "decisions"},
  {"id": "openai/gpt-6-luna-decisions", "label": "GPT-6 Luna Decisions", "interface": "decisions"},
  {"id": "cloudflare/clef",             "label": "Cloudflare Clef",      "interface": "decisions"},
  {"id": "claude-code-opus", "label": "Claude Opus (Claude Code)", "interface": "claude_code",
   "launch": "auto", "claude_model": "opus", "effort": "medium"}
]}
```

The Claude slot needs the Claude Code CLI setup which is described
[below](#claude-code-and-mcp-setup). Starting a run without the CLI installed fails immediately.

## Claude Code and MCP setup

Claude Code cannot be called like an API due to costing constraints. And, I already had a Claude Code subscription that I preferred utilizing. Therefore, the cube is given to it as an MCP server, and the benchmark stays in
charge of the game:

```
Claude Code --stdio--> backend/mcp_server.py --HTTP--> FastAPI backend --> benchmark runner --> web UI
 (tools: get_cube_state, make_move)         (/api/agent/state, /api/agent/move)
```

- `get_cube_state` returns the 54-character cube, the last five moves, the moves left and the 18 allowed moves.
- `make_move(move)` submits one move. The runner validates it, applies it to the cube, logs it, and replies with the
  new state. An invalid move is rejected, counted as malformed, and uses up one of the 30 moves.
- The cube you watch changing in the browser is the one Claude is moving, and every step is logged exactly like the
  other models' steps.

**Automatic mode (used for the results).**
1. Install the Claude Code CLI and sign in once by running `claude`.
2. Keep the `claude_code` entry in `backend/models.json` (shown above). `claude_model` is the model alias, `effort`
   sets the reasoning effort (`low`, `medium`, `high`, `xhigh`, `max`; omit it for the CLI default), and `api_url`
   must be set if the backend is not served on `http://127.0.0.1:8000`.
3. Press **Run benchmark**. For every puzzle the backend runs, in an empty temporary directory:

   ```
   claude -p <task prompt> --model <claude_model> --effort <effort>
          --mcp-config <generated file> --strict-mcp-config
          --allowedTools mcp__cuberace__get_cube_state,mcp__cuberace__make_move --tools ""
   ```

   Only the two cube tools are available, and `ANTHROPIC_API_KEY` is removed from the process environment so Claude
   Code bills the subscription. The puzzle ends when the cube is solved, the move limit is reached, Claude Code
   exits, it never connects within 180 s, or it makes no move for 120 s (the last three count as unsolved).

## How it works

**State and action space.** The cube is a 54-character facelet string (`U R F D L B`, nine stickers per face, read
as seen from outside); the solved cube is `UUUUUUUUURRRRRRRRRFFFFFFFFFDDDDDDDDDLLLLLLLLLBBBBBBBBB`. The action space is
the 18 half-turn-metric moves (`U U' U2 R R' R2 …`). Every model receives the state, its recent moves and the same 18
move definitions, and returns one move.

**Cube engine** (`backend/engine.py`, `backend/cube.py`). The `magiccube` library runs the game. A small
facelet-permutation kernel, about 30× faster and cross-checked against the library, serves the search code.
Scrambles are seeded and reproducible, so every model starts from the same state. The cube counts as solved only when
all six faces are uniform.

**Shortest solution** (`backend/solver.py`). An exact meet-in-the-middle breadth-first search gives the true shortest
solution for the small test sets. Optimality gap = moves used − exact shortest moves. For the long extension
scrambles the reference is Kociemba's two-phase solver (near-optimal, and labelled as such).

**Models** (`backend/adapters/`). Each adapter returns the move, its latency, the raw reply, the confidence when the
API supplies one, and any errors. Invalid replies are logged and counted, never converted into a move.

**Runner and storage** (`backend/benchmark.py`, `backend/service.py`). Every model plays every puzzle in turn from the
same scramble. Each step is appended to `results/<run>/events.jsonl` as it happens (raw reply, latency, retries,
errors, cube state), with a summary in `results/<run>/results.json`. The browser follows a run live over server-sent
events and can replay any saved run at its original pace.

**Interface** (`frontend/`). One lane per model shows the cube, the current move, the move history and the result
(solved, or move limit reached). A statistics table fills in as trials finish, and **Benchmark slide** shows the full
summary: average latency per move, total solve time, moves used, success rate and optimality gap.

## Experiment design

Every rule below applies identically to all four models.

| Rule | Value |
|---|---|
| Starting position | the same seeded scramble for every model |
| Move limit | 30 moves; reaching it is an unsuccessful attempt |
| Test sets | warm-up 3–4 turns · primary 4–5 turns · extension 15–20 turns |
| Move history sent to the model | the last 5 applied moves |
| Timeout | 60 s per call |
| Retries | 1 per step; the retry is sent without the move history, and is logged |
| Invalid or malformed reply | logged, counted as malformed, and uses up one of the 30 moves |
| No answer from a model 3 steps in a row (refusal or outage) | the trial ends as unsolved, with the reason recorded |
| Decision-model question | "Choose the single next move most likely to progress toward the solved cube." plus the 18 move definitions |

The retry drops the history because GPT-6 Luna kept refusing a history that ended in four identical
moves, for example `R' R' R' R'` (the Decisions API reports `502 … refused to answer question`). A failed step changes
nothing, so re-sending the identical request can never succeed. This was tested empirically when performign un-recorded experiments.

**Metrics.** Speed: average latency per move, and total time to solve or to reach the move limit. Quality: solved or
not within 30 moves, total moves used, and the optimality gap on solved puzzles.

## Results

Puzzle: scramble `D' L B2 F`, exact shortest solution **4** moves.

| Model | Solved | Avg latency / move | Total time | Moves used | Optimality gap | Malformed |
|---|---|---|---|---|---|---|
| Jev 1.13 | no (move limit) | 894 ms | 26.8 s | 30 | — | 0 |
| GPT-6 Luna Decisions | no (move limit) | 835 ms | 25.1 s | 30 | — | 0 |
| Cloudflare Clef | no (move limit) | 945 ms | 28.4 s | 30 | — | 0 |
| Claude Opus (Claude Code) | **yes** | 23.0 s | 99.1 s | **4** | **0** | 0 |

- **Opus plans first, then moves instantly.** Its first move took 91.1 s; the next three took 0.2–0.4 s each. Its
  23 s average is dominated by that one planning pause, and its 99 s total is the true cost of the solution.
- **The decision models answer in under a second and never converge.** Jev played only `U` and `U'`, alternating for
  all 30 moves. Luna used four distinct moves, mostly `R'` and `U`. Clef used nine, across the U, F, D and R faces.
  Their mean reported confidence was 0.39, 0.22 and 0.08.
- **The decision models are cheap.** The API reports about $0.0008–$0.0013 per 30-move attempt.

## Answers to the five questions

1. **Which model is fastest at making decisions?** GPT-6 Luna Decisions, at 835 ms per move, with Jev (894 ms) and
   Clef (945 ms) close behind. Opus's 23.0 s average is dominated by a single 91.1 s planning pause; its other three
   moves took 0.2–0.4 s each, faster than any decision model.
2. **Which model achieved the highest solve rate?** Claude Opus, 100%. The three decision models solved nothing (0%).
3. **Which model found the shortest solutions, and how close to the optimum?** Claude Opus, the only model to solve
   the puzzle: 4 moves against an exact optimum of 4, an optimality gap of 0.
4. **Did the fastest model also achieve the best solution quality?** No. The fastest per move (Luna) never reached the
   solved cube, and the best solution came from the slowest decider (Opus).
5. **Fast decision model, frontier LLM, or a hybrid agent in a real application?** A **hybrid agent led by the
   frontier model.**
   - The frontier model is the one that solves the task, and it solved it optimally. It pays its cost once, as a
     planning pause, and then executes at machine speed.
   - Speed per move did not buy quality. Each decision model spent 30 cheap steps and ended where it began.
   - Fast models still belong in the loop for what they do well: they are cheap (about $0.001 per 30 calls) and answer
     in under a second, which suits high-volume, latency-sensitive steps.
   - The routing signal is confidence. All three decision models reported low confidence throughout (0.08–0.39), so
     a fast model answers when it is confident and the frontier model takes over when it is not.

## Notes on the comparison

- Claude Opus runs as an agent in a tool-calling loop and keeps its own earlier moves in context within a puzzle. The
  decision models see only the current state and the last five moves.
- Latency for the decision models is a full HTTP round trip. For Opus it is the time from the last tool reply sent to
  it until its next move arrives, which is its own thinking time without CLI start-up.
- The decision models receive the question wording from the assignment's example. Opus additionally receives an
  explanation of the facelet layout. The longer wording made GPT-6 Luna refuse requests, so the decision models use
  the short one.
- Claude Code runs with no built-in tools, in an empty temporary directory, and is always shut down with the program
  (Stop, Ctrl+C, terminate, or a closed terminal).
