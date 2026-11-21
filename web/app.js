// The monitoring page (docs/ARCHITECTURE.md, section 11). Plain JavaScript and a canvas, no build step.
// At most two event streams are open: the run list and the open run.
"use strict";

const $ = (id) => document.getElementById(id);
const fmt = (v, d = 3) => (v === null || v === undefined ? "–" : Number(v).toFixed(d));

// ------------------------------------------------------------------ run list
const runs = new Map(); // runId -> summary
let listStream = null;

function isSynthetic(r) {
  const s = r.labels && r.labels.synthetic;
  return s === "true" || s === "1";
}

function renderList() {
  const rows = [...runs.values()].filter((r) => !isSynthetic(r))
    .sort((a, b) => (b.createdAt || "").localeCompare(a.createdAt || ""));
  const body = $("runs").querySelector("tbody");
  body.replaceChildren(...rows.map((r) => {
    const tr = document.createElement("tr");
    const when = r.createdAt ? new Date(r.createdAt).toLocaleTimeString() : "";
    for (const v of [when, r.modelId, r.attack, r.epsilon, r.seed, r.status, r.steps]) {
      const td = document.createElement("td");
      td.textContent = v ?? "";
      tr.append(td);
    }
    tr.children[5].className = "status-" + r.status;
    tr.addEventListener("click", () => (location.hash = "run=" + encodeURIComponent(r.runId)));
    return tr;
  }));
  $("empty").hidden = rows.length > 0;
}

function merge(r) {
  runs.set(r.runId, RunList.mergeRun(runs.get(r.runId), r));
}

async function loadRuns() {
  const res = await fetch("/v1/runs?limit=500");
  const data = await res.json();
  for (const r of data.runs) merge(r);
  renderList();
}

function openList() {
  // Open the stream first, then load the list, and merge the two by runId.
  listStream = new EventSource("/v1/events");
  listStream.addEventListener("run", (e) => {
    merge(JSON.parse(e.data));
    renderList();
    if (current && current.runId === JSON.parse(e.data).runId) renderStatus();
  });
  // Load the list once the stream is open (and again after each reconnect: the stream has no ids),
  // so no status change can fall between the two.
  listStream.onopen = () => {
    $("conn").textContent = "";
    loadRuns();
  };
  listStream.onerror = () => {
    $("conn").textContent = "reconnecting…";
  };
}

// ------------------------------------------------------------------ run view
let current = null; // { runId, meta, steps: [], end, shown, stream }

function closeRun() {
  stopPlaying();
  if (current && current.stream) current.stream.close();
  current = null;
}

function openRun(runId) {
  closeRun();
  const run = { runId, meta: null, steps: [], end: null, shown: 0, pos: 0, stream: null };
  current = run;
  $("follow").checked = true;
  $("log-link").href = `/v1/runs/${encodeURIComponent(runId)}/log`;
  $("run-title").textContent = runId;
  const es = new EventSource(`/v1/runs/${encodeURIComponent(runId)}/events`);
  run.stream = es;
  es.addEventListener("meta", (e) => {
    if (current !== run) return;
    run.meta = JSON.parse(e.data);
    renderSettings();
  });
  es.addEventListener("step", (e) => {
    if (current !== run) return;
    const s = JSON.parse(e.data);
    run.steps[s.t] = s;
    onNewStep();
  });
  es.addEventListener("end", (e) => {
    if (current !== run) return;
    run.end = JSON.parse(e.data);
    es.close(); // a finished run: do not let the browser reconnect
    renderStatus();
    renderMarks();
  });
  es.onerror = () => {
    if (current === run && !run.end) $("conn").textContent = "reconnecting…";
  };
  es.onopen = () => ($("conn").textContent = "");
  render();
}

function onNewStep() {
  const n = current.steps.length;
  const slider = $("slider");
  slider.max = String(Math.max(0, n - 1));
  if ($("follow").checked) {
    slider.value = String(n - 1);
    current.shown = n - 1;
  }
  renderMarks();
  render();
}

function renderSettings() {
  const m = current.meta;
  if (!m) return;
  const s = m.settings;
  $("run-title").textContent = `${s.modelId} · ${s.attack === "none" ? "no attack" : s.attack + " ε=" + s.epsilon}`;
  $("run-settings").textContent = `run ${m.runId} · seed ${s.seed} · up to ${s.maxSteps} steps` +
    (s.paceHz ? ` · ${s.paceHz} Hz` : "") + ` · started ${new Date(m.createdAt).toLocaleString()}`;
  $("overlay-toggle").hidden = !Overlay.attacked(m);
}

function runInfo() {
  return (current && runs.get(current.runId)) || {};
}

function renderStatus() {
  if (!current) return;
  const end = current.end;
  const status = end ? end.status : runInfo().status || (current.meta && current.meta.status) || "running";
  const el = $("run-status");
  el.className = "status-" + status;
  if (end) {
    const why = end.reason ? ` (${end.reason}${end.message ? ": " + end.message : ""})` : "";
    const crash = end.crashed ? ", crashed" : "";
    el.textContent = `${status}${why} after ${end.steps} steps${crash}, total reward ${fmt(end.totalReward, 2)}`;
  } else {
    el.textContent = `${status}, ${current.steps.length} steps so far`;
  }
  $("abort").hidden = status !== "running";
}

function renderMarks() {
  const marks = $("marks");
  const n = Math.max(1, current.steps.length - 1);
  const flips = Overlay.flips(current.steps, current.meta);
  marks.replaceChildren(...flips.map((t) => {
    const span = document.createElement("span");
    span.style.left = `${(100 * t) / n}%`;
    span.title = `step ${t}: the attack changed the action`;
    return span;
  }));
}

function render() {
  if (!current) return;
  renderStatus();
  const step = current.steps[current.shown];
  const meta = current.meta;
  const names = (meta && meta.actionNames) || [];
  if (!step) {
    $("step-line").textContent = "waiting for the first step…";
    $("qvalues").querySelector("tbody").replaceChildren();
    drawScene(null);
    return;
  }
  // text line: step, chosen action, clean action, and a mark when they differ
  const line = $("step-line");
  line.replaceChildren();
  line.append(`Step ${step.t}: ${names[step.action] || step.action}`);
  if (Overlay.attacked(meta)) {
    const differs = step.action !== step.cleanAction;
    const span = document.createElement("span");
    span.className = differs ? "flip" : "muted";
    span.textContent = ` · without the attack: ${names[step.cleanAction] || step.cleanAction}` +
      (differs ? "  ✱ changed by the attack" : "");
    line.append(span);
  }
  // Q-values with the highest marked
  const best = step.q.indexOf(Math.max(...step.q));
  $("qvalues").querySelector("tbody").replaceChildren(...step.q.map((q, i) => {
    const tr = document.createElement("tr");
    const a = document.createElement("td");
    a.textContent = names[i] || String(i);
    const v = document.createElement("td");
    v.textContent = fmt(q, 3);
    if (i === best) v.className = "best";
    tr.append(a, v);
    return tr;
  }));
  // attack, epsilon and the largest change
  if (meta) {
    const s = meta.settings;
    $("attack-info").textContent = Overlay.attacked(meta)
      ? `${s.attack}, ε = ${s.epsilon}. Largest change so far ‖δ‖∞ = ${fmt(Overlay.maxLinf(current.steps), 4)}; ` +
        `this step ${fmt(step.linf, 4)}.`
      : "No attack: the agent sees the clean observation.";
  }
  drawScene(sceneAt(step));
}

// While playing, the scene moves continuously from this step's poses to the next step's
// (the final poses after the last step).
function sceneAt(step) {
  const f = current.pos - current.shown;
  if (!playing || f <= 0) return step;
  const next = current.steps[current.shown + 1];
  const to = next ? next.poses : current.end && current.end.finalPoses;
  if (!to) return step;
  const poses = Replay.between(step.poses, to, f);
  // ghosts follow their own cars between steps (Overlay.ghostsAt)
  return { ...step, poses, ghosts: Overlay.ghostsAt(step, next, f, current.meta, poses) };
}

// ------------------------------------------------------------------ continuous replay
let playing = null; // { last: timestamp of the previous frame }

function setPlayButton() {
  $("play").textContent = playing ? "Pause" : "Play";
}

function stopPlaying() {
  playing = null;
  if (current) current.pos = current.shown;
  setPlayButton();
}

function startPlaying() {
  if (!current || current.steps.length < 2) return;
  $("follow").checked = false;
  if (current.shown >= current.steps.length - 1) current.shown = 0; // at the end: play from the start
  current.pos = current.shown;
  playing = { last: null };
  setPlayButton();
  requestAnimationFrame(frame);
}

function frame(ts) {
  if (!playing || !current) return;
  const seconds = playing.last === null ? 0 : (ts - playing.last) / 1000;
  playing.last = ts;
  const r = Replay.advance(current.pos, seconds, Number($("speed").value), current.steps.length);
  current.pos = r.pos;
  current.shown = Math.floor(r.pos);
  $("slider").value = String(current.shown);
  render();
  if (r.done && current.end) stopPlaying(); // a live run may still get more steps: keep waiting
  else requestAnimationFrame(frame);
}

// ------------------------------------------------------------------ the scene
const CAR_L = 5.0, CAR_W = 2.0; // metres

function drawScene(step) {
  const canvas = $("scene");
  const ctx = canvas.getContext("2d");
  const W = canvas.width;
  ctx.clearRect(0, 0, W, canvas.height);
  const meta = current && current.meta;
  if (!meta || !step) return;
  const css = getComputedStyle(document.documentElement);
  const ego = step.poses[0];
  const lanes = (meta.road && meta.road.lanes) || [];
  // fit the road's lateral extent; follow the ego along the road
  let ymin = Infinity, ymax = -Infinity;
  for (const l of lanes) for (const p of l.polyline) { ymin = Math.min(ymin, p[1] - l.width / 2); ymax = Math.max(ymax, p[1] + l.width / 2); }
  if (!isFinite(ymin)) { ymin = ego[2] - 8; ymax = ego[2] + 8; }
  const scale = W / 160; // px per metre: about 160 m of road visible
  const fit = Math.round((ymax - ymin) * scale + 20);
  if (canvas.height !== fit) { canvas.height = fit; return drawScene(step); }
  const cx = ego[1] - W / scale / 3; // ego a third from the left
  const X = (x) => (x - cx) * scale;
  const Y = (y) => 10 + (y - ymin) * scale;

  // lanes: edges solid, separators dashed
  ctx.strokeStyle = css.getPropertyValue("--lane");
  lanes.forEach((l, i) => {
    for (const side of [-1, 1]) {
      const edge = (side === -1 && i === 0) || (side === 1 && i === lanes.length - 1);
      if (side === -1 && i > 0) continue; // drawn by the previous lane
      ctx.setLineDash(edge ? [] : [12, 14]);
      ctx.lineWidth = edge ? 2 : 1;
      ctx.beginPath();
      l.polyline.forEach((p, j) => {
        const x = X(p[0]), y = Y(p[1] + (side * l.width) / 2);
        j ? ctx.lineTo(x, y) : ctx.moveTo(x, y);
      });
      ctx.stroke();
    }
  });
  ctx.setLineDash([]);

  const car = (x, y, heading, fill, alpha = 1, outline = false) => {
    ctx.save();
    ctx.globalAlpha = alpha;
    ctx.translate(X(x), Y(y));
    ctx.rotate(heading || 0);
    ctx.fillStyle = fill;
    ctx.fillRect((-CAR_L / 2) * scale, (-CAR_W / 2) * scale, CAR_L * scale, CAR_W * scale);
    if (outline) {
      ctx.strokeStyle = fill;
      ctx.lineWidth = 1.5;
      ctx.setLineDash([4, 3]);
      ctx.strokeRect((-CAR_L / 2) * scale, (-CAR_W / 2) * scale, CAR_L * scale, CAR_W * scale);
    }
    ctx.restore();
  };
  step.poses.forEach((p, i) => {
    if (i === 0) return;
    car(p[1], p[2], p[3], css.getPropertyValue("--car"));
  });
  car(ego[1], ego[2], ego[3], css.getPropertyValue("--ego"));

  // perturbation overlay: ghosts where the agent believes the observed cars are
  if ($("overlay").checked) {
    for (const g of step.ghosts || Overlay.ghosts(step, meta)) {
      car(g.x, g.y, 0, css.getPropertyValue("--ghost"), 0.45, true);
    }
  }
}

// ------------------------------------------------------------------ controls and routing
$("slider").addEventListener("input", (e) => {
  if (!current) return;
  current.shown = Number(e.target.value);
  current.pos = current.shown; // keeps playing from here if playing
  $("follow").checked = current.shown === current.steps.length - 1 && !current.end;
  render();
});
$("play").addEventListener("click", () => (playing ? stopPlaying() : startPlaying()));
$("follow").addEventListener("change", (e) => {
  if (e.target.checked && current) {
    stopPlaying();
    current.shown = Math.max(0, current.steps.length - 1);
    $("slider").value = String(current.shown);
    render();
  }
});
$("overlay").addEventListener("change", render);
$("abort").addEventListener("click", async () => {
  if (!current) return;
  $("abort").disabled = true;
  try {
    await fetch(`/v1/runs/${encodeURIComponent(current.runId)}`, { method: "DELETE" });
  } finally {
    $("abort").disabled = false;
  }
});

function route() {
  const m = location.hash.match(/run=([^&]+)/);
  if (m) {
    const id = decodeURIComponent(m[1]);
    $("list-view").hidden = true;
    $("run-view").hidden = false;
    if (!current || current.runId !== id) openRun(id);
  } else {
    closeRun();
    $("run-view").hidden = true;
    $("list-view").hidden = false;
  }
}

window.addEventListener("hashchange", route);
openList();
route();
