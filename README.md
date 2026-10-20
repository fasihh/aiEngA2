# Rubik's Cube Race

**AI Engineering, Assignment 02** · Fasih Hasan Khan · K230018

## Demo

Screen recording of the app running the benchmark, from the live run through the final statistics slide (3 min 15 s).
The recording shows a one-puzzle run; the results below come from the full five-puzzle primary benchmark.

<video src="demo/demo.mp4" controls width="100%">
  Your viewer cannot play embedded video. Open <a href="demo/demo.mp4">demo/demo.mp4</a> instead.
</video>

Direct link: [demo/demo.mp4](demo/demo.mp4)

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
nothing, so re-sending the identical request can never succeed. This was tested empirically when performing un-recorded experiments.

**Metrics.** Speed: average latency per move, and total time to solve or to reach the move limit. Quality: solved or
not within 30 moves, total moves used, and the optimality gap on solved puzzles.

## Results

Five primary puzzles (4–5-turn scrambles), every model on every puzzle: **20 trials**. The shortest solutions are
exact, from breadth-first search.

| Puzzle | Scramble | Shortest | Jev 1.13 | GPT-6 Luna | Cloudflare Clef | Claude Opus |
|---|---|---|---|---|---|---|
| 1 | `U2 B U2 R L2` | 5 | move limit | move limit | move limit | **solved, 5 moves** |
| 2 | `U B F' D2 B` | 5 | move limit | ended after 16 moves (no reply) | move limit | **solved, 19 moves** |
| 3 | `R L2 B D` | 4 | move limit | ended after 3 moves (no reply) | move limit | timed out, no move |
| 4 | `R B R B L` | 5 | move limit | move limit | move limit | timed out, no move |
| 5 | `B' R' B2 R2 L'` | 5 | move limit | move limit | move limit | timed out after 2 moves |

| Model | Solved | Success rate | Avg latency / move | Avg total time | Avg moves used | Optimality gap (solved) | Malformed steps |
|---|---|---|---|---|---|---|---|
| Jev 1.13 | 0/5 | 0% | 995 ms | 29.9 s | 30.0 | — | 0 |
| GPT-6 Luna Decisions | 0/5 | 0% | 880 ms | 20.3 s | 21.8 | — | 6 |
| Cloudflare Clef | 0/5 | 0% | 1,025 ms | 30.8 s | 30.0 | — | 0 |
| Claude Opus (Claude Code) | **2/5** | **40%** | 9.3 s | 138.1 s | 5.2 | **+7.0** (0 and +14) | 0 |

- **Opus is the only model that solved anything, and its time arrives in long pauses.** Seven of its 26 steps
  were long pauses of 14–62 s; the other 19 took 0.1–3.7 s. On puzzles 3 and 4 it went
  120 s without making its first move, and on puzzle 5 it made two moves and then went silent for 120 s. Those three
  trials ended at the no-move limit and count as unsolved. Its median step took 0.55 s.
- **Opus's solution quality varied.** Puzzle 1 was optimal (5 moves, gap 0). Puzzle 2 took 19 moves against a shortest
  solution of 5 (gap +14), including a stretch of repeated `R` and `U` turns.
- **The decision models answer in about a second and never converge.** None solved any of its five puzzles. Jev played
  only `U` and `U'` across all 150 steps (85 and 65). Luna used five distinct moves, mostly `U` (60) and `R'` (38).
  Clef used ten. Their mean reported confidence was 0.38, 0.21 and 0.08.
- **The decision models are cheap.** The API reports about $0.005 per model for all five puzzles (Jev $0.0048, Luna
  $0.0046, Clef $0.0051), roughly $0.001 per 30 calls. Opus runs on a subscription and reports no per-call cost.

## Answers to the five questions

1. **Which model is fastest at making decisions?** GPT-6 Luna Decisions, at 880 ms per move, with Jev (995 ms) and
   Clef (1,025 ms) close behind. Opus's 9.3 s average comes from its long pauses between bursts; its median step took
   0.55 s, faster than the median step of any decision model (0.82–0.98 s).
2. **Which model achieved the highest solve rate?** Claude Opus, 40% (2 of 5). The three decision models solved
   nothing (0 of 15 trials).
3. **Which model found the shortest solutions, and how close to the optimum?** Claude Opus, the only model to solve
   a puzzle. It was optimal on one (5 moves against a shortest solution of 5) and took 19 moves on the other
   (shortest 5), an average optimality gap of +7.0 over its two solves.
4. **Did the fastest model also achieve the best solution quality?** No. The fastest per move (Luna) never reached the
   solved cube, and the only solutions came from Opus, which spends far longer deciding.
5. **Fast decision model, frontier LLM, or a hybrid agent in a real application?** A hybrid approach.
   Only the frontier model solved the task: 2 of 5 puzzles, against 0 of 15 trials for the decision models. Moreover, it should not be used alone. Three of its five trials ended at the 120 s no-move limit, and one solution took 19 moves for a 5-move puzzle. A real application has latency/time constraints. Which would mean using system-one for fast and quick decisions but frontier for difficult ones. Additionally, speed per move did not equate quality. The decision models spent up to 30 cheap steps on every puzzle and ended no closer to solved. Fast models still belong in the loop for what they do well since they cost about $0.001 per 30 calls and answer in about a second, which suits high-volume, latency-sensitive steps and fallbacks. The routing signal is confidence as well. All three decision models reported low confidence throughout (0.08–0.38), so a fast model answers when it is confident and the frontier model takes over when it is not.

## Notes on the comparison

- Claude Opus runs as an agent in a tool-calling loop and keeps its own earlier moves in context within a puzzle. The
  decision models see only the current state and the last five moves.
- Latency for the decision models is a full HTTP round trip. For Opus it is the time from the last tool reply sent to
  it until its next move arrives, which is its own thinking time without CLI start-up.
- The no-move limit for Opus is 120 s, the same allowance a decision-model step gets (60 s per call and one retry). It
  decided three of Opus's five trials, so a longer allowance could change those outcomes. Opus's average total time
  (138.1 s) includes that 120 s wait on the trials that ended this way.
- The decision models receive the question wording from the assignment's example. Opus additionally receives an
  explanation of the facelet layout. The longer wording made GPT-6 Luna refuse requests, so the decision models use
  the short one.
- Claude Code runs with no built-in tools, in an empty temporary directory, and is always shut down with the program
  (Stop, Ctrl+C, terminate, or a closed terminal).
