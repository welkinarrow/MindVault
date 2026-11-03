/* MindVault control room. Read-only: every pixel here is derived from logs/events.jsonl
   and the database; the page never decides permissions. The one rule it re-implements
   (can_read, for the "view as" locks) is display only and mirrors server/policy.py. */
"use strict";

const $ = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => [...el.querySelectorAll(s)];
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const LEVEL_NAMES = ["public", "team", "department", "confidential"];
const AGENT_COLORS = { finance: "#f0abfc", intern: "#5eead4", rnd: "#fcd34d" };
const agentColor = (id) => AGENT_COLORS[id] || "#fcd34d";
const fmtTime = (iso) => { const d = new Date(iso); return isNaN(d) ? "" : d.toLocaleTimeString([], { hour12: false }); };
const REASONS = {
  LEVEL_TOO_LOW: "level too low",
  SCOPE_MISMATCH: "scope not readable",
  NOT_FOUND: "not found or quarantined",
  NOT_READ_IN_SESSION: "not read in this session",
  ALREADY_RATED: "already rated",
  INVALID_INPUT: "invalid input",
};

// ------------------------------------------------------------------ state

const S = {
  mode: "live",
  view: "all",
  agents: [],            // from /api/config (keys stripped server-side)
  base: [],              // board at the start of the run
  blocks: new Map(),
  labelMode: null,
  runId: null,
  agentState: {},        // agent -> {session, taint, reads, blocked, writes, last}
  sessions: {},          // session -> {agent, points:[{t, level}]}
  writes: {},            // w- block -> {writer, taint, level, requested}
  pendingQuery: {},      // agent -> READ events of a search, folded into the SEARCH line
  counts: { allow: 0, filtered: 0, denied: 0, writes: 0, rateOk: 0, rateNo: 0, leaks: 0, under: 0 },
  lastWriter: null,
  events: [],            // events applied so far
  t0: null,
};

function agentCfg(id) { return S.agents.find((a) => a.agent_id === id) || { agent_id: id, level: 0, read_scopes: [] }; }
function canRead(agent, b) {
  if (b.status === "quarantined") return false;
  return b.level <= agent.level && (agent.read_scopes || []).includes(b.scope);
}
function ast(id) {
  return (S.agentState[id] ||= { session: null, taint: 0, reads: 0, blocked: 0, writes: 0, last: "" });
}

function resetRun(baseBlocks) {
  S.base = baseBlocks ?? S.base;
  S.blocks = new Map(S.base.map((b) => [b.block_id, { ...b, ratings: [...(b.ratings || [])] }]));
  S.agentState = {};
  S.sessions = {};
  S.writes = {};
  S.pendingQuery = {};
  S.counts = { allow: 0, filtered: 0, denied: 0, writes: 0, rateOk: 0, rateNo: 0, leaks: 0, under: 0 };
  S.lastWriter = null;
  S.events = [];
  S.t0 = null;
  $("#events").innerHTML = "";
  $("#alarm").hidden = true;
}

// ------------------------------------------------------------------ event reducer

function apply(e, anim) {
  S.events.push(e);
  if (S.t0 === null) S.t0 = Date.parse(e.time);
  const a = e.agent;
  switch (e.event) {
    case "START": {
      const keepBase = S.base;
      resetRun(keepBase);
      S.events.push(e);
      S.t0 = Date.parse(e.time);
      S.labelMode = e.label_mode;
      S.runId = e.run_id;
      pushEv(e, "START", null, `Service started · <code>LABEL_MODE=${esc(e.label_mode)}</code>${e.run_id ? ` · run <code>${esc(e.run_id)}</code>` : ""}`);
      if (S.mode === "live" && anim) refreshLiveBlocks();
      break;
    }
    case "SESSION": {
      const st = ast(a);
      st.session = e.session; st.taint = 0;
      S.sessions[e.session] = { agent: a, points: [{ t: Date.parse(e.time), level: 0 }] };
      st.last = "new session";
      pushEv(e, "SESSION", a, `New session <code>${esc(e.session)}</code>. Taint starts at <b>L0</b>.`);
      break;
    }
    case "READ": onRead(e, anim); break;
    case "SEARCH": onSearch(e, anim); break;
    case "WRITE": onWrite(e, anim); break;
    case "REVIEW": onReview(e, anim); break;
    case "RATE": onRate(e, anim); break;
    default: pushEv(e, e.event, a, esc(JSON.stringify(e)));
  }
  if (S.labelMode === null && e.label_mode) S.labelMode = e.label_mode;
  if (S.runId === null && e.run_id) S.runId = e.run_id;
}

function bumpTaint(agent, session, level, time) {
  const st = ast(agent);
  if (level > st.taint) {
    st.taint = level;
    const s = S.sessions[session] ||= { agent, points: [{ t: time, level: 0 }] };
    s.points.push({ t: time, level });
  }
}

function onRead(e, anim) {
  const a = e.agent, st = ast(a), b = S.blocks.get(e.block);
  if (e.session && !st.session) st.session = e.session;
  if (e.result === "ALLOW") {
    S.counts.allow++; st.reads++;
    bumpTaint(a, e.session, e.level ?? 0, Date.parse(e.time));
    st.last = `read ${e.block}`;
    if (anim) { flash(e.block, "hit-allow"); beam(a, e.block, "var(--allow)"); flashAgent(a, "allow"); }
    checkLeak(e, anim);
  } else if (e.result === "FILTERED") {
    S.counts.filtered++; st.blocked++;
    if (anim) { flash(e.block, "hit-filter"); beam(a, e.block, "var(--filter)"); }
  } else {
    S.counts.denied++; st.blocked++;
    st.last = `denied ${e.block}`;
    if (anim) { flash(e.block, "hit-deny"); beam(a, e.block, "var(--deny)"); flashAgent(a, "deny"); }
  }
  if (e.via === "query") {
    (S.pendingQuery[a] ||= []).push(e);
    return;
  }
  const lv = e.level != null ? ` <span class="badge">L${e.level}/${esc(e.scope)}</span>` : "";
  if (e.result === "ALLOW") {
    pushEv(e, "READ", a, `Read <code>${esc(e.block)}</code> by id${lv}`, `Taint is now L${st.taint}.`);
  } else {
    const why = reasonText(e, a, b);
    pushEv(e, "READ", a, `Read <code>${esc(e.block)}</code> by id${lv} → <b>denied</b>`, why);
  }
}

function reasonText(e, agentId, b) {
  const ag = agentCfg(agentId);
  if (e.reason_code === "LEVEL_TOO_LOW") return `${esc(agentId)} is L${ag.level}; the block is L${e.level}. Returned <code>LEVEL_TOO_LOW</code>.`;
  if (e.reason_code === "SCOPE_MISMATCH") return `${esc(agentId)} can read ${esc((ag.read_scopes || []).join(", "))}, not <code>${esc(e.scope)}</code>. Returned <code>SCOPE_MISMATCH</code>.`;
  if (e.reason_code === "NOT_FOUND") return `Missing or quarantined; both return <code>NOT_FOUND</code> so quarantined content is not revealed.`;
  return esc(REASONS[e.reason_code] || e.reason_code || "");
}

function checkLeak(e, anim) {
  const w = S.writes[e.block];
  const reader = agentCfg(e.agent);
  if (!w || w.writer === e.agent) return;
  if (w.taint > reader.level) {
    S.counts.leaks++;
    const msg = `<code>${esc(e.agent)}</code> (L${reader.level}) read <code>${esc(e.block)}</code>, written by ${esc(w.writer)} after reading L${w.taint} material but labelled L${w.level}.`;
    pushEv(e, "LEAK", e.agent, msg, "This is the failure session taint is meant to stop.", "alert");
    if (anim) {
      flash(e.block, "hit-leak");
      $("#alarm").hidden = false;
      $("#alarmText").innerHTML = msg;
    }
  }
}

function onSearch(e, anim) {
  const a = e.agent, st = ast(a);
  const reads = S.pendingQuery[a] || [];
  S.pendingQuery[a] = [];
  if (typeof e.taint_after === "number") bumpTaint(a, e.session, e.taint_after, Date.parse(e.time));
  st.last = `searched "${e.query}"`;
  const ret = (e.returned || []).map((id) => {
    const b = S.blocks.get(id);
    return `<span class="badge" style="color:var(--allow)">${esc(id)}${b ? ` L${b.level}` : ""}</span>`;
  }).join(" ");
  const filt = reads.filter((r) => r.result === "FILTERED").map((r) =>
    `<span class="badge" style="color:var(--filter)" title="${esc(REASONS[r.reason_code] || r.reason_code)}">✕ ${esc(r.block)} L${r.level} ${esc(r.reason_code === "LEVEL_TOO_LOW" ? "level too high" : "wrong scope")}</span>`).join(" ");
  pushEv(e, "SEARCH", a,
    `Searched <code>${esc(e.query)}</code><div style="margin-top:5px;display:flex;gap:4px;flex-wrap:wrap">${ret || '<span class="hint">no results</span>'}</div>` +
    (filt ? `<div style="margin-top:4px;display:flex;gap:4px;flex-wrap:wrap">${filt}</div>` : ""),
    `${(e.returned || []).length} returned with full content, ${(e.filtered || []).length} filtered by permission. Taint after search: <b>L${e.taint_after ?? st.taint}</b>.`);
}

function onWrite(e, anim) {
  const a = e.agent, st = ast(a);
  st.writes++; S.counts.writes++; S.lastWriter = a;
  const under = e.taint_level != null && e.level < e.taint_level;
  if (under) S.counts.under++;
  const basis = e.level_basis === "agent_declared"
    ? `The agent declared <b>L${e.level}</b>; session taint would have given <b>L${e.taint_level}</b>.`
    : `Labelled <b>L${e.level}</b> = the highest level this session read. The agent cannot choose it.`;
  if (e.block) {
    S.writes[e.block] = { writer: a, taint: e.taint_level ?? e.level, level: e.level, requested: e.requested_level };
    const prev = S.blocks.get(e.block);
    S.blocks.set(e.block, {
      ...(prev || {}), block_id: e.block, title: e.title || prev?.title || "(written block)",
      content: prev?.content || "", level: e.level, scope: e.scope, status: e.result, author: a,
      written: true, under, taint_level: e.taint_level, ratings: prev?.ratings || [],
    });
    st.last = `wrote ${e.block}`;
    if (anim) { renderBoard(); flash(e.block, "hit-write"); beam(a, e.block, "var(--write)"); }
    pushEv(e, "WRITE", a,
      `Wrote <code>${esc(e.block)}</code> <span class="badge">L${e.level}/${esc(e.scope)}</span> “${esc(e.title || "")}”`,
      basis + (under ? ` <b style="color:var(--filter)">Under-labelled by ${e.taint_level - e.level}.</b>` : "") +
      (e.context_blocks?.length ? ` Reviewer context: ${e.context_blocks.map(esc).join(", ")}.` : ""),
      under ? "warn" : "");
  } else {
    st.last = "write rejected";
    pushEv(e, "WRITE", a, `Write <b>rejected</b> by review, not stored: “${esc(e.title || "")}”`, basis, "warn");
  }
}

function onReview(e, anim) {
  const b = e.block && S.blocks.get(e.block);
  if (b) { b.review_action = e.result; b.review_reason = e.reason; }
  const verdict = { pass: "passed → stored as unverified", quarantine: "quarantined → no agent can read it", reject: "rejected → not stored" }[e.result] || e.result;
  pushEv(e, "REVIEW", null, `${e.block ? `<code>${esc(e.block)}</code> ` : ""}${esc(verdict)}`, esc(e.reason || ""),
    e.result === "pass" ? "" : "warn");
  if (anim && e.block) flash(e.block, e.result === "pass" ? "hit-allow" : "hit-deny");
}

function onRate(e, anim) {
  const a = e.agent;
  const b = S.blocks.get(e.block);
  if (e.result === "ACCEPTED") {
    S.counts.rateOk++;
    if (b) b.ratings = [...(b.ratings || []), { agent_id: a, accuracy: e.accuracy }];
    if (anim) { flash(e.block, "hit-allow"); beam(a, e.block, "var(--rate)"); }
    pushEv(e, "RATE", a, `Rated <code>${esc(e.block)}</code> ${"★".repeat(Number(e.accuracy) || 0)}`, "Accepted: read in this session.");
  } else {
    S.counts.rateNo++;
    if (anim) { flash(e.block, "hit-deny"); beam(a, e.block, "var(--deny)"); }
    pushEv(e, "RATE", a, `Rating for <code>${esc(e.block)}</code> <b>rejected</b>`,
      `<code>${esc(e.reason_code)}</code>: ${esc(REASONS[e.reason_code] || "")}. Only blocks actually read can be rated.`, "warn");
  }
}

// ------------------------------------------------------------------ stream items

let renderStream = true;
function pushEv(e, kind, who, msg, why = "", cls = "") {
  if (!renderStream) return;
  const li = document.createElement("li");
  li.className = `ev ${cls}`;
  const rcls = e.event === "READ" ? ` r-${e.result}` : "";
  li.innerHTML = `<time>${fmtTime(e.time)}</time><div>
    <div class="h"><span class="kind k-${kind}${rcls}">${kind}</span>${who ? `<span class="who" style="color:${agentColor(who)}">${esc(who)}</span>` : ""}</div>
    <div class="msg">${msg}</div>${why ? `<div class="why">${why}</div>` : ""}</div>`;
  const list = $("#events");
  list.prepend(li);
  while (list.children.length > 400) list.lastChild.remove();
}

// ------------------------------------------------------------------ rendering

function renderAll() {
  renderHeader(); renderAgents(); renderBoard(); renderStats(); renderChart();
  $("#evCount").textContent = `${S.events.length} events`;
  if (!$("#events").children.length) $("#events").innerHTML = `<li class="empty">${S.mode === "live" ? "Waiting for the service… start it and run an agent." : "Press ▶ to replay."}</li>`;
}
function renderLight() {
  renderAgents(); renderStats(); renderChart(); renderBadges();
  $("#evCount").textContent = `${S.events.length} events`;
  $(".empty", $("#events"))?.remove();
}

function renderHeader() {
  const lm = $("#labelMode");
  lm.textContent = S.labelMode ? (S.labelMode === "taint" ? "label: session taint" : "label: agent self-labels (baseline)") : "label: —";
  lm.className = `chip ${S.labelMode || ""}`;
  $("#runId").textContent = `run: ${S.runId || "—"}`;
  const seg = $("#viewSeg");
  $$("button", seg).forEach((b) => b.remove());
  for (const id of ["all", ...S.agents.map((a) => a.agent_id)]) {
    const b = document.createElement("button");
    b.dataset.view = id; b.textContent = id;
    if (S.view === id) b.classList.add("on");
    b.onclick = () => { S.view = id; renderHeader(); renderBoard(); };
    seg.append(b);
  }
}

function renderAgents() {
  const box = $("#agentList");
  box.innerHTML = S.agents.map((ag) => {
    const st = ast(ag.agent_id);
    const col = agentColor(ag.agent_id);
    const bars = [0, 1, 2, 3].map((i) => `<i class="${i <= st.taint && st.session ? "on" : ""}"></i>`).join("");
    return `<div class="agent ${st.session ? "active" : ""}" data-agent="${esc(ag.agent_id)}">
      <div class="agent-top"><div class="avatar" style="background:${col}">${esc(ag.agent_id[0].toUpperCase())}</div>
        <div><div class="agent-name">${esc(ag.agent_id)}</div><div class="agent-sub">clearance L${ag.level} · writes to <span class="mono">${esc(ag.write_scope)}</span></div></div></div>
      <div class="scopes">${(ag.read_scopes || []).map((s) => `<span class="tag">${esc(s)}</span>`).join("")}</div>
      <div class="meter-k"><span>session taint</span><span>${st.session ? "L" + st.taint : "no session"}</span></div>
      <div class="taintbar">${bars}</div>
      <div class="counts"><div><b>${st.reads}</b><span>reads</span></div><div><b style="color:${st.blocked ? "var(--filter)" : "inherit"}">${st.blocked}</b><span>blocked</span></div><div><b>${st.writes}</b><span>writes</span></div></div>
      <div class="last">${esc(st.last)}</div></div>`;
  }).join("") || `<div class="empty">No agent config found.</div>`;
}

function renderBoard() {
  const viewer = S.view === "all" ? null : agentCfg(S.view);
  const floors = [3, 2, 1, 0].map((l) => {
    const cards = [...S.blocks.values()].filter((b) => b.level === l)
      .sort((x, y) => (x.written === y.written ? x.block_id.localeCompare(y.block_id) : x.written ? 1 : -1))
      .map((b) => cardHTML(b, viewer)).join("");
    return `<div class="floor" data-l="${l}"><div class="floor-label"><b>L${l}</b>${LEVEL_NAMES[l]}</div><div class="cards">${cards}</div></div>`;
  }).join("");
  $("#floors").innerHTML = floors;
  $$(".card", $("#floors")).forEach((c) => c.onclick = () => openDrawer(c.dataset.id));
}

function cardHTML(b, viewer) {
  const locked = viewer && !canRead(viewer, b);
  const cls = ["card", b.status, b.written ? "written" : "", locked ? "locked" : ""].join(" ");
  return `<div class="${cls}" data-id="${esc(b.block_id)}" data-l="${b.level}" title="${esc(b.title)}">
    <div class="id"><span>${esc(b.block_id)}</span><span style="color:var(--dim)">${esc(b.scope)}</span></div>
    <div class="t">${esc(b.title)}</div>
    <div class="badges">${badgesHTML(b)}</div></div>`;
}

function badgesHTML(b) {
  const out = [];
  if (b.status === "unverified") out.push(`<span class="badge">unverified</span>`);
  if (b.written && b.review_action) out.push(`<span class="badge ${esc(b.review_action)}">${esc(b.review_action)}</span>`);
  if (b.under) out.push(`<span class="badge under" title="Taint would have labelled it L${b.taint_level}">UNDER L${b.taint_level}→L${b.level}</span>`);
  if (b.ratings?.length) {
    const avg = b.ratings.reduce((s, r) => s + Number(r.accuracy || 0), 0) / b.ratings.length;
    out.push(`<span class="badge star">★ ${avg.toFixed(1)}·${b.ratings.length}</span>`);
  }
  return out.join("");
}
function renderBadges() {
  for (const el of $$(".card", $("#floors"))) {
    const b = S.blocks.get(el.dataset.id);
    if (b) $(".badges", el).innerHTML = badgesHTML(b);
  }
}

function setStat(id, value, foot) {
  const el = $(id);
  if (el.textContent !== String(value)) { el.textContent = value; el.closest(".stat").classList.remove("bump"); void el.offsetWidth; el.closest(".stat").classList.add("bump"); }
  if (foot !== undefined) $(id + "F").innerHTML = foot;
}

function renderStats() {
  const c = S.counts;
  setStat("#sAllow", c.allow);
  setStat("#sBlocked", c.filtered + c.denied, `filtered ${c.filtered} · denied ${c.denied}`);
  setStat("#sWrites", c.writes, c.under ? `<span style="color:var(--filter)">${c.under} under-labelled</span>` : "reviewed before they land");
  setStat("#sRates", c.rateOk + c.rateNo, `accepted ${c.rateOk} · rejected ${c.rateNo}`);
  setStat("#sLeaks", c.leaks, c.leaks ? "a reader saw material above its level" : "reader below the writer's taint");
  $("#sLeaks").className = `stat-v ${c.leaks ? "bad" : ""}`;
  $("#sLeaks").closest(".stat").classList.toggle("leak", c.leaks > 0);
  const who = S.lastWriter || (S.agents.find((a) => ast(a.agent_id).session && a.level > 0) || {}).agent_id;
  const t = who ? ast(who).taint : 0;
  setStat("#sTaint", who ? `L${t}` : "L0");
  $("#sTaint").style.color = `var(--l${t})`;
  $$("#sTaintBar i").forEach((i, k) => i.classList.toggle("on", !!who && k <= t));
  $("#sTaint").closest(".stat").querySelector(".stat-k").textContent = who ? `${who}'s session taint` : "Session taint";
}

function renderChart() {
  const svg = $("#taintChart");
  const W = 640, H = 190, L = 34, R = 10, T = 12, B = 22;
  const sessions = Object.values(S.sessions);
  const times = S.events.map((e) => Date.parse(e.time)).filter((t) => !isNaN(t));
  const tmin = times.length ? Math.min(...times) : 0, tmax = times.length ? Math.max(...times) : 1;
  const span = Math.max(tmax - tmin, 1);
  const x = (t) => L + ((t - tmin) / span) * (W - L - R);
  const y = (l) => T + (3 - l) / 3 * (H - T - B);
  let g = "";
  for (let l = 0; l <= 3; l++) g += `<line class="gridline" x1="${L}" x2="${W - R}" y1="${y(l)}" y2="${y(l)}"/><text class="axis" x="6" y="${y(l) + 3}">L${l}</text>`;
  if (times.length) g += `<text class="axis" x="${L}" y="${H - 5}">${fmtTime(new Date(tmin).toISOString())}</text><text class="axis" x="${W - R}" y="${H - 5}" text-anchor="end">${fmtTime(new Date(tmax).toISOString())}</text>`;
  for (const s of sessions) {
    const pts = [...s.points];
    const end = S.events.length ? Date.parse(S.events[S.events.length - 1].time) : pts[0].t;
    let d = "", prev = null;
    for (const p of pts) {
      d += prev === null ? `M${x(p.t)},${y(p.level)}` : `H${x(p.t)}V${y(p.level)}`;
      prev = p;
    }
    d += `H${x(end)}`;
    const col = agentColor(s.agent);
    g += `<path class="series" d="${d}" stroke="${col}"/>`;
    for (const p of pts.slice(1)) g += `<circle class="pt" cx="${x(p.t)}" cy="${y(p.level)}" r="3.5" fill="${col}"/>`;
    g += `<text class="axis" x="${Math.min(x(end) + 0, W - R)}" y="${y(pts[pts.length - 1].level) - 6}" text-anchor="end" fill="${col}" style="fill:${col}">${esc(s.agent)}</text>`;
  }
  // mark writes: the label each write got vs the taint it carried
  for (const e of S.events) {
    if (e.event !== "WRITE" || e.level == null) continue;
    const xx = x(Date.parse(e.time));
    const under = e.taint_level != null && e.level < e.taint_level;
    g += `<line x1="${xx}" x2="${xx}" y1="${T}" y2="${H - B}" stroke="${under ? "var(--filter)" : "var(--write)"}" stroke-dasharray="3 4" opacity=".7"/>`;
    g += `<rect x="${xx - 4}" y="${y(e.level) - 4}" width="8" height="8" rx="2" fill="${under ? "var(--filter)" : "var(--write)"}"><title>${esc(e.block || "rejected")} labelled L${e.level}</title></rect>`;
  }
  svg.innerHTML = g;
}

// ------------------------------------------------------------------ animation helpers

function flash(id, cls) {
  const el = $(`.card[data-id="${CSS.escape(id || "")}"]`);
  if (!el) return;
  el.classList.remove(cls); void el.offsetWidth; el.classList.add(cls);
  setTimeout(() => el.classList.remove(cls), 1600);
}
function flashAgent(id, kind) {
  const el = $(`.agent[data-agent="${CSS.escape(id || "")}"]`);
  if (!el) return;
  el.classList.add(`flash-${kind}`);
  setTimeout(() => el.classList.remove(`flash-${kind}`), 700);
}
function beam(agentId, blockId, color) {
  const svg = $("#beams"), vault = $(".vault");
  const from = $(`.agent[data-agent="${CSS.escape(agentId || "")}"] .avatar`);
  const to = $(`.card[data-id="${CSS.escape(blockId || "")}"]`);
  if (!from || !to) return;
  const vr = vault.getBoundingClientRect(), fr = from.getBoundingClientRect(), tr = to.getBoundingClientRect();
  const x1 = fr.right - vr.left, y1 = fr.top + fr.height / 2 - vr.top;
  const x2 = tr.left - vr.left + 4, y2 = tr.top + tr.height / 2 - vr.top;
  const mx = (x1 + x2) / 2;
  const p = document.createElementNS("http://www.w3.org/2000/svg", "path");
  p.setAttribute("d", `M${x1},${y1} C${mx},${y1} ${mx},${y2} ${x2},${y2}`);
  p.setAttribute("class", "beam");
  p.setAttribute("stroke", color);
  svg.append(p);
  setTimeout(() => p.remove(), 1700);
}

// ------------------------------------------------------------------ drawer

function openDrawer(id) {
  const b = S.blocks.get(id);
  if (!b) return;
  const who = S.agents.map((ag) => {
    const ok = canRead(ag, b);
    const why = ok ? "can read" : b.status === "quarantined" ? "quarantined" : b.level > ag.level ? `L${ag.level} < L${b.level}` : `no ${b.scope} scope`;
    return `<div><span class="${ok ? "ok" : "no"}">${ok ? "●" : "○"}</span> <b>${esc(ag.agent_id)}</b> <span class="hint">${esc(why)}</span></div>`;
  }).join("");
  const w = S.writes[id];
  $("#drawerBody").innerHTML = `
    <div class="mono" style="color:var(--l${b.level})">${esc(b.block_id)}</div>
    <h3>${esc(b.title)}</h3>
    <div class="meta"><span class="badge">L${b.level} ${LEVEL_NAMES[b.level]}</span><span class="badge">${esc(b.scope)}</span><span class="badge">${esc(b.status)}</span><span class="badge">by ${esc(b.author || "seed")}</span></div>
    <div class="content">${b.content ? esc(b.content) : '<span class="hint">Content is not in the event log. In live mode it loads from the database.</span>'}</div>
    ${w ? `<div class="sec"><h4>Label</h4><div>${w.requested != null ? `Agent declared L${w.level}. ` : ""}Session taint at write time: <b>L${w.taint}</b>. ${w.level < w.taint ? '<b style="color:var(--filter)">Under-labelled.</b>' : "Label matches the taint."}</div></div>` : ""}
    ${b.review_reason ? `<div class="sec"><h4>Review</h4><div><span class="badge ${esc(b.review_action)}">${esc(b.review_action)}</span> ${esc(b.review_reason)}</div></div>` : ""}
    <div class="sec"><h4>Who can read it</h4><div class="who-can">${who}</div></div>
    ${b.ratings?.length ? `<div class="sec"><h4>Ratings</h4>${b.ratings.map((r) => `<div>${esc(r.agent_id)} ${"★".repeat(Number(r.accuracy) || 0)} <span class="hint">${esc(r.reason || "")}</span></div>`).join("")}</div>` : ""}`;
  $("#drawer").hidden = false;
}
$("#drawerX").onclick = () => ($("#drawer").hidden = true);

// ------------------------------------------------------------------ HW7 panel

async function loadResults() {
  const box = $("#hw7");
  let r;
  try { r = await (await fetch("/api/results")).json(); } catch { r = { available: false }; }
  if (!r.available || !r.rows?.length) {
    box.innerHTML = `<div class="hw7-empty">Waiting for benchmark results. When <code>bench/results/results.csv</code> exists (from <code>bench/score.py</code>), leak rate, correctness and over-restriction appear here for taint vs self, per run set.</div>`;
    $("#hw7src").textContent = "no results yet";
    return;
  }
  $("#hw7src").textContent = r.source;
  const tags = [...new Set(r.rows.map((x) => x.tag))];
  const metrics = [["leak", "Leak rate", true], ["correct", "Correctness", false], ["over_restricted", "Over-restriction", true]];
  let html = "";
  for (const tag of tags) {
    const rows = r.rows.filter((x) => x.tag === tag);
    if (tags.length > 1 || tag) html += `<div class="hint" style="margin:4px 0 8px">set: <b class="mono">${esc(tag || "all")}</b></div>`;
    for (const [m, label, lowerBetter] of metrics) {
      if (!rows.some((x) => x[m] !== undefined)) continue;
      html += `<div class="hw7-row"><div class="k">${label}<div class="hint">${lowerBetter ? "lower is better" : "higher is better"}</div></div><div class="bars">${
        rows.map((x) => `<div class="bar"><span class="c">${esc(x.condition)}</span><div class="track"><div class="fill ${esc(x.condition)}" style="width:${Math.round((x[m] ?? 0) * 100)}%"></div></div><span class="v">${x[m] === undefined ? "—" : Math.round(x[m] * 100) + "%"}</span></div>`).join("")
      }</div></div>`;
    }
    const cost = rows.map((x) => `${esc(x.condition)}: ${x.runs} runs${x.wall_time_s !== undefined ? `, ${x.wall_time_s}s/run` : ""}${x.tool_calls !== undefined ? `, ${x.tool_calls} tool calls/run` : ""}`).join(" · ");
    html += `<div class="hw7-note">${cost}</div>`;
  }
  box.innerHTML = html || `<div class="hw7-empty">${esc(r.source)} has ${r.raw_rows} runs but no scored columns yet (leak, correct, over_restricted).</div>`;
}

// ------------------------------------------------------------------ live mode

let es = null;
function setConn(state, text) {
  const c = $("#conn");
  c.className = `chip ${state}`;
  c.innerHTML = `<i class="dot"></i>${text}`;
}

async function startLive() {
  stopReplay();
  const snap = await (await fetch("/api/live")).json();
  renderStream = true;
  resetRun(snap.blocks);
  S.labelMode = null; S.runId = snap.run_id;
  // Rebuild state from the current run without animation; the board already holds written blocks.
  for (const e of snap.events) apply(e, false);
  renderAll();
  if (es) es.close();
  es = new EventSource("/api/stream");
  es.onopen = () => setConn("ok", "live");
  es.onerror = () => setConn("bad", "reconnecting");
  es.onmessage = (m) => {
    let e;
    try { e = JSON.parse(m.data); } catch { return; }
    apply(e, true);
    if (e.event === "START") renderAll(); else renderLight();
  };
}

async function refreshLiveBlocks() {
  setTimeout(async () => {
    const r = await (await fetch("/api/blocks")).json();
    S.base = r.blocks;
    for (const b of r.blocks) if (!S.blocks.has(b.block_id) || !S.blocks.get(b.block_id).written) S.blocks.set(b.block_id, { ...b });
    renderBoard();
  }, 400);
}

// fill in content of written blocks from the database when it arrives (live mode)
async function hydrateWritten() {
  if (S.mode !== "live") return;
  const r = await (await fetch("/api/blocks")).json();
  for (const b of r.blocks) {
    const cur = S.blocks.get(b.block_id);
    if (cur && cur.written && !cur.content) cur.content = b.content;
  }
}

// ------------------------------------------------------------------ replay mode

const R = { events: [], i: 0, playing: false, speed: 1, timer: null, source: null };

async function loadSources() {
  const r = await (await fetch("/api/sources")).json();
  const sel = $("#source");
  const groups = { sample: "Scripted samples", bench: "Benchmark runs", log: "Service runs in logs/" };
  sel.innerHTML = Object.entries(groups).map(([k, label]) => {
    const items = r.sources.filter((s) => s.kind === k);
    return items.length ? `<optgroup label="${label}">${items.map((s) => `<option value="${esc(s.id)}" title="${esc(s.note)}">${esc(s.label)}</option>`).join("")}</optgroup>` : "";
  }).join("");
  if (!r.sources.length) sel.innerHTML = `<option value="">no recordings yet</option>`;
}

async function loadReplay(source) {
  stopReplay();
  if (!source) return;
  const r = await (await fetch(`/api/replay?source=${encodeURIComponent(source)}`)).json();
  if (r.error) return;
  R.source = source; R.events = r.events; R.i = 0;
  S.labelMode = null; S.runId = null;
  resetRun(r.blocks);
  renderStream = true;
  $("#scrub").max = R.events.length; $("#scrub").value = 0;
  renderAll();
  updatePos();
}

function stepReplay(anim = true) {
  if (R.i >= R.events.length) { pauseReplay(); return false; }
  const e = R.events[R.i++];
  apply(e, anim);
  if (e.event === "START" || e.event === "WRITE") renderAll(); else renderLight();
  updatePos();
  return true;
}

function nextDelay() {
  if (R.i >= R.events.length || R.i === 0) return 400;
  const a = Date.parse(R.events[R.i - 1].time), b = Date.parse(R.events[R.i].time);
  const gap = Math.min(Math.max(b - a, 0), 6000);
  return Math.max(260, Math.min(gap * 0.5 + 260, 2200)) / R.speed;
}

function playReplay() {
  if (R.i >= R.events.length) seekReplay(0);
  R.playing = true; $("#bPlay").textContent = "❚❚";
  const tick = () => {
    if (!R.playing) return;
    if (!stepReplay(true)) return;
    R.timer = setTimeout(tick, nextDelay());
  };
  tick();
}
function pauseReplay() { R.playing = false; clearTimeout(R.timer); $("#bPlay").textContent = "▶"; }
function stopReplay() { pauseReplay(); }

function seekReplay(n) {
  pauseReplay();
  resetRun(S.base);
  S.labelMode = null; S.runId = null;
  renderStream = true;
  R.i = 0;
  while (R.i < n) apply(R.events[R.i++], false);
  renderAll();
  updatePos();
}
function updatePos() {
  $("#pos").textContent = `${R.i} / ${R.events.length}`;
  $("#scrub").value = R.i;
}

// ------------------------------------------------------------------ mode switching and wiring

function setMode(mode) {
  S.mode = mode;
  $$("#modeSeg button").forEach((b) => b.classList.toggle("on", b.dataset.mode === mode));
  $("#replayBar").hidden = mode !== "replay";
  $("#drawer").hidden = true;
  if (mode === "live") {
    startLive();
  } else {
    if (es) { es.close(); es = null; }
    setConn("", "replay");
    loadSources().then(() => loadReplay($("#source").value));
  }
}

$$("#modeSeg button").forEach((b) => (b.onclick = () => setMode(b.dataset.mode)));
$("#source").onchange = (e) => loadReplay(e.target.value);
$("#bPlay").onclick = () => (R.playing ? pauseReplay() : playReplay());
$("#bStep").onclick = () => { pauseReplay(); stepReplay(true); };
$("#bBack").onclick = () => seekReplay(0);
$("#scrub").oninput = (e) => seekReplay(Number(e.target.value));
$$("#speedSeg button").forEach((b) => (b.onclick = () => {
  R.speed = Number(b.dataset.s);
  $$("#speedSeg button").forEach((x) => x.classList.toggle("on", x === b));
}));

document.addEventListener("keydown", (e) => {
  if (e.target.matches("input, select, textarea")) return;
  if (e.key === "Escape") $("#drawer").hidden = true;
  if (e.key === "l" || e.key === "L") setMode("live");
  if (e.key === "r" || e.key === "R") setMode("replay");
  if (S.mode !== "replay") return;
  if (e.key === " ") { e.preventDefault(); R.playing ? pauseReplay() : playReplay(); }
  if (e.key === "ArrowRight") { pauseReplay(); stepReplay(true); }
  if (e.key === "Home") seekReplay(0);
});

window.addEventListener("resize", () => $("#beams").innerHTML = "");
setInterval(() => { hydrateWritten(); }, 3000);
setInterval(loadResults, 30000);

(async function init() {
  const cfg = await (await fetch("/api/config")).json();
  S.agents = cfg.agents;
  loadResults();
  const want = new URLSearchParams(location.search).get("mode");
  setMode(want === "replay" ? "replay" : "live");
})();
