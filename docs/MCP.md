# Playing the cube with Claude Code (MCP)

This lets your own Claude (Opus, through your Claude subscription) play the benchmark instead of
paying for OpenRouter tokens. The cube you see changing in the web UI is the one Claude is moving.

```
Claude Code ──stdio──▶ backend/mcp_server.py ──HTTP──▶ FastAPI backend ──▶ runner ──▶ web UI
 (tools: get_cube_state, make_move)     (/api/agent/state, /api/agent/move)
```

The MCP server only forwards. The benchmark runner stays in charge: it validates each move,
applies it to the cube, counts it, logs it, and streams it to the browser.

> **Not an apples-to-apples contract.** The OpenRouter models each answer one question per state.
> Claude Code is an *agent*: it calls tools in a loop and remembers its earlier moves within a puzzle.
> Treat its numbers as "Opus 5.5 as an agent" and say so in your write-up.
> The assignment also says Opus should go through OpenRouter, so a subscription run is a deviation; note it
> in the README or check with your instructor.

## 0. One-time setup: install Claude Code

Claude Code is not installed on this machine yet. Either of these (check the official docs if a command has changed):

```bash
curl -fsSL https://claude.ai/install.sh | bash
# or
npm install -g @anthropic-ai/claude-code
```

Then run `claude` once and sign in with your **Claude subscription** (the `/login` flow). Check:

```bash
claude --version
```

## Mode A, automatic: press Run and the backend starts Claude Code

1. In `backend/models.json`, set `"enabled": true` on the `claude-code-opus` entry (it is disabled by default so
   the mock models still run without the CLI). Optional fields:
   - `"claude_model"`: `"opus"` (the alias for the newest Opus) or a full id such as `"claude-opus-5-5"`
   - `"api_url"`: backend URL if it is not on `http://127.0.0.1:8000`
   - `"extra_args"`: extra CLI flags, as a list
2. Start the backend and open the UI:
   ```bash
   uv run uvicorn backend.main:app
   ```
3. Click **Run benchmark**. For each puzzle the backend launches `claude -p ...` with the cube MCP server
   attached through a generated `--mcp-config` file, in an empty temp directory, with no built-in tools (only the
   two cube tools) and `--strict-mcp-config`. When the puzzle ends it waits for the process to exit.

What it does for you:
- `ANTHROPIC_API_KEY` is removed from the child's environment so Claude Code bills your **subscription**, not an API key
  (set `"use_subscription": false` to keep it).
- If `claude` is not installed, **Run benchmark** fails immediately with a message pointing here.
- If Claude Code crashes or never connects, that trial is marked unsolved and the lane shows the reason
  (plus the last lines of Claude's output).

## Turning reasoning down

Claude Code has no "thinking off" switch, but `effort` is the closest thing. In `backend/models.json` set
`"effort": "low"` (allowed: `low`, `medium`, `high`, `xhigh`, `max`; omit it for the CLI default), which passes
`--effort low` to Claude Code. Measured on the same cube question with Opus:

| setting | output tokens | model time |
|---|---|---|
| default | about 800 | 12 s |
| `--effort low` | 10 | 1.6 s |
| `CLAUDE_CODE_DISABLE_THINKING=1` | about 680 | 12 s (no effect) |

So `effort: low` makes Opus answer almost immediately, at the cost of guessing more. The `claude-code-opus-low` entry is
a ready-made variant; enabling both entries gives you a thinking-vs-instant comparison for the same model.
`CLAUDE_CODE_DISABLE_THINKING=1` made no difference on this CLI version. `MAX_THINKING_TOKENS=0` was not measured
(the test run hung and was stopped), so don't rely on it.

## Mode B, manual: attach your own Claude Code session

Use this when you want to watch and talk to the session yourself.

1. Set `"launch": "manual"` (and `"enabled": true`) on the `claude-code-opus` entry in `backend/models.json`.
2. Register the MCP server with Claude Code. Use absolute paths. No `--env` is needed: the server already defaults to
   `http://127.0.0.1:8000`, and `--env` is variadic, so it swallows the server name that follows it.

   ```bash
   claude mcp add --transport stdio --scope user cuberace -- \
     /home/fasih/Desktop/code/sem-repos/aiEngA2/.venv/bin/python \
     /home/fasih/Desktop/code/sem-repos/aiEngA2/backend/mcp_server.py
   ```

   `--scope user` makes it available from any folder; drop it to register for the current project only.
   If your backend is not on port 8000, use the `.mcp.json` form below and set `CUBE_API_URL` in its `env`.
   Equivalent project-level `.mcp.json` (put it in the folder you launch `claude` from):

   ```json
   {
     "mcpServers": {
       "cuberace": {
         "command": "/home/fasih/Desktop/code/sem-repos/aiEngA2/.venv/bin/python",
         "args": ["/home/fasih/Desktop/code/sem-repos/aiEngA2/backend/mcp_server.py"],
         "env": { "CUBE_API_URL": "http://127.0.0.1:8000" }
       }
     }
   }
   ```

3. Check it registered: `claude mcp list`, or type `/mcp` inside a Claude Code session. You should see
   `cuberace` connected with two tools, `get_cube_state` and `make_move`.
4. Start the backend (`uv run uvicorn backend.main:app`), open the UI, and click **Run benchmark**.
   The Claude lane says "Waiting for you to attach Claude Code…".
5. In your Claude Code session (approve the two tools when asked, or allow `mcp__cuberace`), send:

   > Use the cuberace MCP tools to solve the Rubik's Cube. Call get_cube_state, then make one move at a time with
   > make_move, choosing the single move most likely to progress toward solved. When a puzzle ends (solved or
   > limit_reached), call get_cube_state again to pick up the next puzzle. Stop once get_cube_state keeps
   > returning "waiting". Don't use any other tools.

`get_cube_state` waits (up to 25 s per call) for the next puzzle, so one conversation can play the whole run.
Use `/clear` between runs; within a run your session remembers earlier puzzles, which the automatic mode avoids.

## How timing is measured

A move's latency is the time from the **last tool response sent to Claude** (the first `get_cube_state`, then
each `make_move`) to the **next `make_move` request arriving**. That is Claude's own thinking and generation time
plus a few milliseconds of localhost and stdio overhead. CLI start-up, sign-in checks and process spawning are
not included. The first move is timed from the first `get_cube_state`, so start-up is excluded there too.
Differences from the OpenRouter models, worth stating in your write-up:
- Claude Code adds its own system prompt and a multi-turn tool loop, so each step carries a little more overhead than a raw API call.
- If Claude calls `get_cube_state` between moves, that time counts inside the move (it is part of deciding).
- The same limits apply as for every model: 30 moves, an invalid move is counted and wastes a move, and a step
  with no move for 120 s ends the puzzle as unsolved.

## Troubleshooting

| Symptom | Likely cause |
|---|---|
| `cuberace` missing or failed in `/mcp` | Wrong paths, or the venv is missing: run `uv sync` and re-add with absolute paths |
| Tool result says "Could not reach the benchmark backend" | Backend not running, or on another port: set `CUBE_API_URL` / `api_url` |
| Tool result says "waiting" | No puzzle is active: click Run benchmark in the UI |
| Trial unsolved, error mentions "exited (code N)" | Claude Code failed to start; the lane shows its last output. Try `claude -p "hi"` yourself (is it signed in?) |
| CLI rejects a flag | Flags vary by version; see below |

**Flags to verify after installing.** The automatic mode was tested against a stand-in `claude` script that checks
the arguments, not against the real CLI (it is not installed here). It uses `-p`, `--model`, `--mcp-config`,
`--strict-mcp-config`, `--allowedTools` and `--tools ""`. If your version rejects one, run
`claude --help`, then fix `ClaudeCodeMCPAdapter._command` in `backend/adapters/claude_code.py` or pass replacements
through `extra_args`.
