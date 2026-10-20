"use strict";

const $ = (id) => document.getElementById(id);
const h = (s) => String(s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

// ---- identity header (STUDENT_NAME / STUDENT_ROLL_NO from the backend .env) --
function setStudent({ name, roll }) {
  for (const [n, r] of [["studentName", "studentRoll"], ["slideName", "slideRoll"]]) {
    $(n).textContent = name;
    $(r).textContent = roll;
  }
}

// ---- formatting ------------------------------------------------------------
const fmtLatency = (s) => (s < 1 ? `${Math.round(s * 1000)} ms` : `${s.toFixed(2)} s`);
const fmtTime = (s) => (s >= 60 ? `${(s / 60).toFixed(1)} min` : `${s.toFixed(1)} s`);
const fmtNum = (x, d = 1) => (x == null ? "—" : x.toFixed(d));

// ---- cube net (SVG from the 54-char facelet string, order U R F D L B) -----
const COLORS = { U: "#f4f4f4", R: "#d62e2e", F: "#1f9d4d", D: "#f2cc1b", L: "#f07d14", B: "#2457d6" };
const FACE_ORIGIN = { U: [3, 0], L: [0, 3], F: [3, 3], R: [6, 3], B: [9, 3], D: [3, 6] };
const FACE_ORDER = "URFDLB";
const SOLVED = FACE_ORDER.split("").map((f) => f.repeat(9)).join("");

function cubeSVG(state) {
  let rects = "";
  FACE_ORDER.split("").forEach((face, fi) => {
    const [ox, oy] = FACE_ORIGIN[face];
    for (let i = 0; i < 9; i++) {
      const c = state[fi * 9 + i];
      rects += `<rect x="${ox + (i % 3)}" y="${oy + Math.floor(i / 3)}" width="1" height="1" rx=".12" fill="${COLORS[c] || "#888"}"/>`;
    }
  });
  return `<svg viewBox="-.1 -.1 12.2 9.2" stroke="#1b1f27" stroke-width=".07" role="img" aria-label="cube net">${rects}</svg>`;
}

// ---- app state ---------------------------------------------------------------
let models = [];            // from /api/models: {id,label,category,...}
let lanes = {};             // model id -> lane object
let puzzles = {};           // puzzle id -> puzzle
let run = null;             // {id, set, n, maxMoves, total, done}
let currentPuzzle = null;
let es = null;
let lastSummary = {};
let replay = null;          // {id, events, i, paused, speed, fast, timer} while replaying a recorded run
let runList = [];           // from /api/runs
let liveSeq = 0;            // guards against out-of-order live-summary responses

function buildLanes(list) {
  $("lanes").innerHTML = "";
  lanes = {};
  for (const m of list) {
    const el = document.createElement("article");
    el.className = "lane";
    el.innerHTML = `
      <div class="lane-head">
        <div><h3>${h(m.label || m.id)}</h3><span class="cat">${h(m.category || m.id)}</span></div>
        <span class="badge">Waiting</span>
      </div>
      <div class="cube">${cubeSVG(SOLVED)}</div>
      <div class="now"><span class="move">—</span><span class="meta">no move yet</span></div>
      <div class="history"></div>
      <div class="result"></div>`;
    $("lanes").appendChild(el);
    lanes[m.id] = {
      el, moves: 0,
      badge: el.querySelector(".badge"), cube: el.querySelector(".cube"),
      move: el.querySelector(".move"), meta: el.querySelector(".meta"),
      history: el.querySelector(".history"), result: el.querySelector(".result"),
    };
  }
}

function setBadge(lane, text, kind) {
  lane.badge.textContent = text;
  lane.badge.className = "badge" + (kind ? " " + kind : "");
  lane.el.classList.toggle("active", kind === "playing");
  lane.el.classList.toggle("solved", kind === "solved");
  lane.el.classList.toggle("failed", kind === "failed");
}

function resetLane(lane, state) {
  lane.moves = 0;
  lane.cube.innerHTML = cubeSVG(state);
  lane.move.textContent = "—";
  lane.meta.textContent = "no move yet";
  lane.history.innerHTML = "";
  lane.result.textContent = "";
  setBadge(lane, "Waiting", "");
}

function setStatus(text) {
  $("status").textContent = (replay ? `REPLAY ${replay.id} · ` : "") + text;
  $("status").classList.toggle("replay", !!replay);
}

// mode: "idle" | "live" (a benchmark is running) | "replay" (playing back a recorded run)
function setControls(mode) {
  const idle = mode === "idle";
  $("runBtn").disabled = !idle;
  $("stopBtn").disabled = mode !== "live";
  for (const id of ["setSelect", "nInput", "seedInput"]) $(id).disabled = !idle;
  $("runSelect").disabled = !idle;
  $("replayBtn").disabled = !idle || !selectedReplayable();
  for (const id of ["pauseBtn", "skipBtn", "exitReplayBtn"]) $(id).disabled = mode !== "replay";
  if (idle) $("pauseBtn").textContent = "Pause";
}

function setRunning(on) { setControls(on ? "live" : "idle"); }

function updateProgress() {
  const pct = run && run.total ? (100 * run.done) / run.total : 0;
  $("progressBar").style.width = pct + "%";
  $("progressText").textContent = run ? `${run.done}/${run.total} trials` : "";
}

// ---- event handlers ----------------------------------------------------------
const handlers = {
  run_start(e) {
    puzzles = Object.fromEntries(e.puzzles.map((p) => [p.id, p]));
    const recorded = e.models.map((m) => (typeof m === "string" ? { id: m, label: m } : m));
    models = recorded.map((m) => ({ ...(models.find((x) => x.id === m.id) || {}), ...m }));
    buildLanes(models);
    run = { id: e.run_id || (replay && replay.id), set: e.set, n: e.puzzles.length, maxMoves: e.max_moves, total: e.puzzles.length * e.models.length, done: 0 };
    currentPuzzle = null;
    lastSummary = {};
    $("puzzleBar").hidden = false;
    $("statsNote").textContent = `run ${e.run_id} · ${e.set} · ${run.n} puzzles`;
    $("statsTable").innerHTML = '<div class="empty">Statistics appear as trials finish.</div>';
    updateProgress();
    setStatus("Running…");
  },

  trial_start(e) {
    if (e.puzzle !== currentPuzzle) {
      currentPuzzle = e.puzzle;
      const p = puzzles[e.puzzle];
      $("puzzleId").textContent = `${e.puzzle} (${p.scramble_len}-turn scramble)`;
      $("puzzleScramble").textContent = p.scramble.join(" ");
      $("puzzleOptimal").textContent = p.optimal + (p.optimal_is_exact ? "" : " (reference)");
      for (const l of Object.values(lanes)) resetLane(l, p.initial_state);
    }
    const lane = lanes[e.model];
    if (lane) setBadge(lane, "Playing", "playing");
    setStatus(`Running · ${e.puzzle} · ${(models.find((m) => m.id === e.model) || {}).label || e.model}`);
  },

  move(e) {
    const lane = lanes[e.model];
    if (!lane) return;
    lane.history.querySelectorAll(".last").forEach((c) => c.classList.remove("last"));
    const chip = document.createElement("span");
    chip.className = "chip last" + (e.valid ? "" : " bad");
    chip.textContent = e.valid ? e.move : "✗";
    chip.title = e.valid ? `step ${e.step}` : `step ${e.step}: ${e.errors.join("; ")}`;
    lane.history.appendChild(chip);
    lane.history.scrollTop = lane.history.scrollHeight;
    lane.cube.innerHTML = cubeSVG(e.state_after);
    lane.move.textContent = e.valid ? e.move : "invalid";
    const conf = e.confidence != null ? ` · conf ${(e.confidence * 100).toFixed(0)}%` : "";
    const retry = e.retries ? ` · ${e.retries} retr${e.retries > 1 ? "ies" : "y"}` : "";
    lane.meta.textContent = `step ${e.step}/${run.maxMoves} · ${fmtLatency(e.latency_s)}${conf}${retry}`;
  },

  trial_end(e) {
    const t = e.trial, lane = lanes[e.model];
    run.done++;
    updateProgress();
    if (lane) {
      if (t.solved) {
        setBadge(lane, "Solved ✓", "solved");
        const gap = t.gap == null ? "" : ` · gap ${t.gap >= 0 ? "+" : ""}${t.gap}`;
        lane.result.textContent = `Solved in ${t.moves_applied} moves (shortest ${t.optimal})${gap} · ${fmtTime(t.total_time_s)}`;
      } else {
        setBadge(lane, "Move limit reached ✗", "failed");
        lane.result.textContent = `Not solved after ${t.steps_used} steps · ${t.malformed_steps} malformed · ${fmtTime(t.total_time_s)}` +
          (t.error ? ` · ${t.error}` : "");
      }
    }
    refreshLiveStats();
  },

  agent_status(e) {
    const lane = lanes[e.model];
    if (lane) lane.meta.textContent = e.text;
    setStatus(`${(models.find((m) => m.id === e.model) || {}).label || e.model}: ${e.text}`);
  },

  run_end(e) {
    liveSeq++;              // drop any live-summary fetch still in flight
    lastSummary = e.summary || {};
    renderDashboard($("statsTable"), lastSummary);
    setStatus(e.status === "stopped" ? "Stopped" : "Finished");
    finishRun();
  },

  run_error(e) {
    setStatus("Error: " + e.error);
    finishRun();
  },
};

function finishRun() {
  if (replay) { endReplay(); return; }
  if (es) { es.close(); es = null; }
  setRunning(false);
  loadRuns();
}

async function refreshLiveStats() {
  if (replay && replay.fast) return;   // skipping ahead: the final summary is rendered at the end
  const seq = ++liveSeq;
  try {
    const r = await fetch(`/api/runs/${run.id}/live-summary?upto=${run.done}`);
    if (!r.ok) return;
    const data = await r.json();
    if (seq !== liveSeq) return; // a newer update (or run_end) superseded this one
    lastSummary = data.summary;
    renderDashboard($("statsTable"), lastSummary);
  } catch (_) { /* next trial will retry */ }
}

function attach(runId) {
  if (es) es.close();
  setRunning(true);
  es = new EventSource(`/api/benchmark/stream?run_id=${encodeURIComponent(runId)}`);
  for (const [type, fn] of Object.entries(handlers)) {
    es.addEventListener(type, (ev) => fn(JSON.parse(ev.data)));
  }
}

// ---- dashboard -----------------------------------------------------------------
const COLS = [
  { key: "avg_move_latency_s", label: "Avg latency / move", fmt: fmtLatency, best: "min" },
  { key: "avg_total_time_s", label: "Avg total time", fmt: fmtTime, best: "min" },
  { key: "avg_moves", label: "Avg moves", fmt: (x) => fmtNum(x), best: null },
  { key: "success_rate_pct", label: "Success rate", fmt: (x) => x.toFixed(0) + "%", best: "max" },
  { key: "avg_optimality_gap", label: "Optimality gap", fmt: (x) => (x == null ? "—" : (x >= 0 ? "+" : "") + x.toFixed(2)), best: "min" },
  { key: "malformed_steps", label: "Malformed", fmt: (x) => x, best: null },
];

function bestOf(rows, col) {
  const vals = rows.map((r) => r[col.key]).filter((v) => v != null);
  if (!col.best || !vals.length) return null;
  return col.best === "min" ? Math.min(...vals) : Math.max(...vals);
}

function chartHTML(title, rows, key, fmt) {
  const vals = rows.map((r) => r[key] ?? 0);
  const max = Math.max(...vals, 1e-9);
  return `<div class="chart"><h4>${h(title)}</h4>${rows.map((r) => `
    <div class="bar-row"><span class="label">${h(r.label)}</span>
      <span class="track"><span class="fill" style="width:${(100 * (r[key] ?? 0)) / max}%;display:block"></span></span>
      <span class="val">${r[key] == null ? "—" : fmt(r[key])}</span></div>`).join("")}</div>`;
}

function renderDashboard(el, summary) {
  const rows = Object.entries(summary).map(([id, s]) => ({ id, ...s }));
  if (!rows.length) { el.innerHTML = '<div class="empty">No finished trials yet.</div>'; return; }
  const best = Object.fromEntries(COLS.map((c) => [c.key, bestOf(rows, c)]));
  el.innerHTML = `<div class="table-wrap"><table>
    <thead><tr><th>Model</th><th>Trials</th>${COLS.map((c) => `<th>${c.label}</th>`).join("")}</tr></thead>
    <tbody>${rows.map((r) => `<tr><td>${h(r.label)}</td><td>${r.solved}/${r.trials} solved</td>${COLS.map((c) =>
      `<td class="${best[c.key] != null && r[c.key] === best[c.key] ? "best" : ""}">${r[c.key] == null ? "—" : c.fmt(r[c.key])}</td>`).join("")}</tr>`).join("")}
    </tbody></table></div>
    <div class="charts">
      ${chartHTML("Avg latency per move", rows, "avg_move_latency_s", fmtLatency)}
      ${chartHTML("Success rate", rows, "success_rate_pct", (x) => x.toFixed(0) + "%")}
      ${chartHTML("Avg moves used", rows, "avg_moves", (x) => x.toFixed(1))}
      ${chartHTML("Avg optimality gap (solved runs)", rows, "avg_optimality_gap", (x) => x.toFixed(2))}
    </div>`;
}

function answersHTML(summary) {
  const rows = Object.entries(summary).map(([id, s]) => ({ id, ...s }));
  if (!rows.length) return "";
  const by = (key, dir) => rows.filter((r) => r[key] != null).sort((a, b) => dir * (a[key] - b[key]));
  const fastest = by("avg_move_latency_s", 1)[0];
  const topRate = Math.max(...rows.map((r) => r.success_rate_pct));
  const bestSolvers = rows.filter((r) => r.success_rate_pct === topRate);
  const closest = by("avg_optimality_gap", 1)[0];
  const sameWinner = closest && fastest.id === closest.id;
  return `<div class="answers"><ol>
    <li><b>Fastest decisions:</b> ${h(fastest.label)} (${fmtLatency(fastest.avg_move_latency_s)} per move).</li>
    <li><b>Highest solve rate:</b> ${h(bestSolvers.map((r) => r.label).join(", "))} (${topRate.toFixed(0)}%).</li>
    <li><b>Closest to the shortest solution:</b> ${closest
      ? `${h(closest.label)}, average gap ${closest.avg_optimality_gap >= 0 ? "+" : ""}${closest.avg_optimality_gap.toFixed(2)} moves, ${closest.optimal_solves} optimal solve${closest.optimal_solves === 1 ? "" : "s"}.`
      : "no model solved a puzzle with a known optimum."}</li>
    <li><b>Fastest also best quality?</b> ${closest ? (sameWinner ? "Yes, " + h(fastest.label) + " is both fastest and closest to optimal." : `No. ${h(fastest.label)} is fastest, but ${h(closest.label)} has the best solution quality.`) : "Not determinable: no solved puzzles."}</li>
    <li><b>Fast model, frontier LLM or hybrid?</b> <i>Write your own conclusion here, using the numbers above.</i></li>
  </ol></div>`;
}

function openSlide() {
  const r = run;
  $("slideMeta").textContent = r ? `Run ${r.id} · ${r.set} set · ${r.n} puzzles · ${r.maxMoves}-move limit` : "No run yet";
  const body = $("slideBody");
  renderDashboard(body, lastSummary);
  body.insertAdjacentHTML("beforeend", answersHTML(lastSummary));
  $("slide").hidden = false;
}

// ---- previous runs + replay -----------------------------------------------------
function selectedReplayable() {
  const r = runList.find((x) => x.run_id === $("runSelect").value);
  return !!(r && r.replayable);
}

async function loadRuns() {
  try {
    runList = await fetch("/api/runs").then((r) => r.json());
  } catch (_) { return; }
  const keep = $("runSelect").value;
  $("runSelect").innerHTML = runList.length
    ? runList.map((r) => {
        const when = new Date(r.created).toLocaleString();
        const label = `${r.run_id} · ${r.set || "?"} · ${r.n_puzzles} puzzles · ${r.models.length} models · ${when} · ${r.status}`;
        return `<option value="${h(r.run_id)}"${r.replayable ? "" : " disabled"}>${h(label)}</option>`;
      }).join("")
    : "<option value=''>No recorded runs yet</option>";
  if (runList.some((r) => r.run_id === keep && r.replayable)) $("runSelect").value = keep;
  else {
    const first = runList.find((r) => r.replayable);
    if (first) $("runSelect").value = first.run_id;
  }
  if (!replay && !es) setControls("idle");
}

// How long to wait before showing each recorded event: a move appears after the
// latency the model really took; the other events get a short beat.
function delayFor(e) {
  if (e.type === "move") return e.latency_s * 1000;
  if (e.type === "trial_start") return 500;
  if (e.type === "trial_end") return 300;
  if (e.type === "agent_status") return 0;
  return 0;
}

function dispatch(e) {
  const fn = handlers[e.type];
  if (fn) fn(e);
}

function replayStep() {
  if (!replay || replay.paused) return;
  if (replay.i >= replay.events.length) { endReplay(true); return; }
  const e = replay.events[replay.i];
  // Schedule against a running clock, not "now + delay", so timer overhead does not
  // accumulate and the replay keeps the recorded pace.
  replay.nextAt += delayFor(e) / replay.speed;
  replay.timer = setTimeout(() => {
    if (!replay) return;
    replay.i++;
    dispatch(e);
    replayStep();
  }, Math.max(0, replay.nextAt - performance.now()));
}

async function startReplay(id) {
  if (replay || es) return;
  setStatus("Loading recorded run…");
  const r = await fetch(`/api/runs/${encodeURIComponent(id)}/events`);
  if (!r.ok) { setStatus("Cannot replay: " + r.status); return; }
  const events = await r.json();
  replay = { id, events, i: 0, paused: false, speed: +$("speedSelect").value, fast: false, timer: null, nextAt: performance.now() };
  setControls("replay");
  setStatus("Starting…");
  replayStep();
}

// `ranOut` is true when the log ended without a run_end event (the original run crashed).
async function endReplay(ranOut = false) {
  if (!replay) return;
  clearTimeout(replay.timer);
  const id = replay.id;
  if (ranOut && run) {
    const data = await fetch(`/api/runs/${id}/live-summary`).then((r) => r.json());
    lastSummary = data.summary;
    renderDashboard($("statsTable"), lastSummary);
  }
  const note = ranOut ? "Replay finished (the original run did not complete)" : "Replay finished";
  replay = null;
  setStatus(note);
  setControls("idle");
}

function exitReplay() {
  if (!replay) return;
  clearTimeout(replay.timer);
  replay = null;
  setStatus("Replay stopped");
  setControls("idle");
}

function skipReplay() {
  if (!replay) return;
  clearTimeout(replay.timer);
  replay.fast = true;
  replay.paused = false;
  const events = replay.events;
  while (replay && replay.i < events.length) dispatch(events[replay.i++]);  // may end the replay via run_end
  if (replay) endReplay(true);
}

$("replayBtn").onclick = () => startReplay($("runSelect").value);
$("runSelect").onchange = () => setControls("idle");
$("exitReplayBtn").onclick = exitReplay;
$("skipBtn").onclick = skipReplay;
$("speedSelect").onchange = () => { if (replay) replay.speed = +$("speedSelect").value; };
$("pauseBtn").onclick = () => {
  if (!replay) return;
  replay.paused = !replay.paused;
  $("pauseBtn").textContent = replay.paused ? "Resume" : "Pause";
  if (replay.paused) clearTimeout(replay.timer);
  else { replay.nextAt = performance.now(); replayStep(); }
};

// ---- controls -------------------------------------------------------------------
$("slideBtn").onclick = openSlide;
$("slideClose").onclick = () => { $("slide").hidden = true; };
document.addEventListener("keydown", (e) => { if (e.key === "Escape") $("slide").hidden = true; });

$("runBtn").onclick = async () => {
  setStatus("Starting…");
  const body = { set: $("setSelect").value, n: +$("nInput").value, seed: +$("seedInput").value };
  const r = await fetch("/api/benchmark/start", { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify(body) });
  const data = await r.json();
  if (!r.ok) { setStatus("Cannot start: " + (data.detail || r.status)); return; }
  attach(data.run_id);
};

$("stopBtn").onclick = async () => {
  await fetch("/api/benchmark/stop", { method: "POST" });
  setStatus("Stopping after the current trial…");
};

async function init() {
  fetch("/api/student").then((r) => r.json()).then(setStudent);
  const [sets, mods, status] = await Promise.all([
    fetch("/api/sets").then((r) => r.json()),
    fetch("/api/models").then((r) => r.json()),
    fetch("/api/benchmark/status").then((r) => r.json()),
  ]);
  models = mods;
  $("setSelect").innerHTML = Object.entries(sets)
    .map(([k, v]) => `<option value="${k}">${k} (${v.min_turns}–${v.max_turns} turns)</option>`).join("");
  $("setSelect").value = "primary";
  buildLanes(models);
  await loadRuns();
  if (status.status === "running") attach(status.run_id); // page refreshed mid-run: replay + go live
}
init();
