const byId = (id) => document.getElementById(id);
const actions = ["Lane left", "Idle", "Lane right", "Faster", "Slower"];
const features = ["Presence", "X", "Y", "VX", "VY"];
const methods = {random_linf: "Random L∞", pgd: "PGD", minbest: "MinBest", worst_q: "Worst-Q"};
const list = byId("runs");
const slider = byId("timeline");
const play = byId("play");
const canvas = byId("road");
const ctx = canvas.getContext("2d");
let selected = "";
let socket;
let run;
let records = [];
let cursor = 0;
let following = true;
let playback;

const put = (id, content) => { byId(id).textContent = content; };
const value = (n, digits = 4) => Number(n).toFixed(digits);

function table(id, rows, picked = -1) {
  byId(id).replaceChildren(...rows.map((cols, index) => {
    const row = document.createElement("tr");
    if (index === picked) row.className = "picked";
    for (const col of cols) {
      const cell = row.insertCell();
      cell.textContent = col;
    }
    return row;
  }));
}

function draw(poses = []) {
  const {width, height} = canvas;
  ctx.fillStyle = "#131d21";
  ctx.fillRect(0, 0, width, height);
  if (!run?.lanes?.length) return;
  const ego = poses.find((vehicle) => vehicle.ego);
  const lanes = [...run.lanes].sort((a, b) => a.y - b.y);
  const minY = lanes[0].y - lanes[0].width / 2;
  const last = lanes.at(-1);
  const maxY = last.y + last.width / 2;
  const laneY = (y) => 25 + (y - minY) / (maxY - minY) * (height - 50);
  const origin = (ego?.x ?? 0) - 35;
  const roadX = (x) => (x - origin) / 150 * width;
  ctx.fillStyle = "#27343c";
  ctx.fillRect(0, 25, width, height - 50);
  ctx.lineWidth = 2;
  ctx.strokeStyle = "#84969e";
  for (const y of [minY, ...lanes.slice(1).map((l) => l.y - l.width / 2), maxY]) {
    ctx.setLineDash(y === minY || y === maxY ? [] : [24, 18]);
    ctx.lineDashOffset = -roadX(0) % 42;
    ctx.beginPath();
    ctx.moveTo(0, laneY(y));
    ctx.lineTo(width, laneY(y));
    ctx.stroke();
  }
  ctx.setLineDash([]);
  for (const vehicle of [...poses].sort((a, b) => Number(a.ego) - Number(b.ego))) {
    const x = roadX(vehicle.x);
    const y = laneY(vehicle.y);
    ctx.save();
    ctx.translate(x, y);
    ctx.rotate(vehicle.heading);
    ctx.fillStyle = vehicle.ego ? "#80edc7" : "#a5b4be";
    ctx.beginPath();
    ctx.roundRect(-18, -12, 36, 24, 5);
    ctx.fill();
    ctx.fillStyle = "#15282b";
    ctx.fillRect(3, -10, 6, 20);
    ctx.restore();
    if (vehicle.ego) {
      ctx.fillStyle = "#80edc7";
      ctx.font = "14px system-ui";
      ctx.fillText(`Ego · ${value(vehicle.speed, 1)} m/s`, x + 24, y - 17);
    }
  }
  canvas.setAttribute("aria-label", ego
    ? `Four-lane highway with ${poses.length} vehicles. Ego speed ${value(ego.speed, 1)} meters per second.`
    : "Four-lane simulated highway");
}

function render() {
  const r = records[cursor];
  slider.max = Math.max(0, records.length - 1);
  slider.value = cursor;
  slider.disabled = !r;
  play.disabled = records.length < 2;
  byId("live").disabled = !r;
  put("step", r ? `${cursor + 1} / ${records.length}` : "—");
  draw(r?.vehicles_before);
  put("action", r ? actions[r.action] : "—");
  put("comparison", r ? `Clean action: ${actions[r.clean_action]} · ${r.action === r.clean_action ? "Same action" : "Action changed"}` : "");
  put("method", r ? (r.perturbation.epsilon ? methods[r.perturbation.method] : "Clean") : "—");
  put("epsilon", r ? value(r.perturbation.epsilon) : "—");
  put("linf", r ? value(r.perturbation.linf, 6) : "—");
  put("reward", r ? value(r.reward, 3) : "—");
  put("gateway-ms", r ? `${value(r.gateway_decision_ms, 2)} ms` : "—");
  put("outcome", r ? (r.crashed ? "Collision after action" : r.terminated || r.truncated ? "Episode ended after action" : "") : "");
  table("scores", r ? actions.map((a, i) => [a, value(r.clean_scores[i]), value(r.scores[i])]) : [], r?.action);
  table("features", r ? r.observation.map((n, i) => [
    Math.floor(i / 5) + 1, features[i % 5], value(n), value(r.perturbed_observation[i]),
    value(r.perturbed_observation[i] - n, 6)
  ]) : []);
  table("poses", r ? r.vehicles_before.map((v) => [
    v.ego ? "Ego" : v.id, value(v.x, 2), value(v.y, 2), value(v.heading, 3), value(v.speed, 2)
  ]) : []);
}

function showStatus() {
  put("status", run
    ? `${run.config.model.group} · checkpoint seed ${run.config.model.seed} · episode seed ${run.config.seed} · ${run.status}${run.reason ? " · " + run.reason : ""}`
    : "Start a vehicle client to see a run.");
}

function watch(id) {
  if (id !== selected) return;
  const scheme = location.protocol === "https:" ? "wss:" : "ws:";
  const link = new WebSocket(`${scheme}//${location.host}/ws/runs/${id}?after=${records.at(-1)?.seq ?? -1}`);
  let rejected = false;
  socket = link;
  link.onmessage = ({data}) => {
    if (socket !== link) return;
    const event = JSON.parse(data);
    if (event.type === "run" || event.type === "status") {
      run = event.run;
      showStatus();
    }
    if (event.type === "step") {
      records.push(event.record);
      if (following) cursor = records.length - 1;
    }
    if (event.type === "error") {
      rejected = true;
      put("status", event.message);
    }
    render();
  };
  link.onclose = () => {
    if (socket === link && !rejected && (!run || run.status === "running")) setTimeout(() => {
      if (socket === link) watch(id);
    }, 1000);
  };
}

function stop() {
  clearInterval(playback);
  playback = undefined;
  play.textContent = "Play";
}

function select(id) {
  stop();
  socket?.close();
  socket = undefined;
  selected = id;
  run = undefined;
  records = [];
  cursor = 0;
  following = true;
  byId("monitor").hidden = !id;
  const download = byId("download");
  download.hidden = !id;
  download.href = id ? `/runs/${id}/log` : "#";
  showStatus();
  render();
  if (id) watch(id);
}

async function refresh() {
  try {
    const response = await fetch("/runs");
    if (!response.ok) throw Error();
    const runs = await response.json();
    const prior = [...list.options].find((option) => option.value && option.value === selected);
    list.replaceChildren(new Option("Select a run", ""), ...runs.map((item) => {
      const {model, seed, method, epsilon} = item.config;
      const condition = epsilon ? `${methods[method]} ε ${epsilon}` : "Clean";
      const label = `${model.group} #${model.seed} · seed ${seed} · ${condition} · ${item.status} · ${item.id.slice(0, 8)}`;
      return new Option(label, item.id);
    }));
    if (prior && !runs.some(({id}) => id === selected)) list.add(prior);
    if (!selected && runs.length) select(runs[0].id);
    list.value = selected;
    put("connection", "Gateway connected");
  } catch {
    put("connection", "Gateway unavailable");
  }
}

list.onchange = () => select(list.value);
slider.oninput = () => { stop(); following = false; cursor = Number(slider.value); render(); };
byId("live").onclick = () => { stop(); following = true; cursor = Math.max(0, records.length - 1); render(); };
play.onclick = () => {
  if (playback) { stop(); return; }
  following = false;
  if (cursor >= records.length - 1) cursor = 0;
  render();
  play.textContent = "Pause";
  playback = setInterval(() => {
    if (cursor >= records.length - 1) stop();
    else { cursor += 1; render(); }
  }, 500);
};
refresh();
setInterval(refresh, 2500);
