"use strict";

// Every value goes in as a text node, so fixture, model or user text can't become markup.
const byId = (id) => document.getElementById(id);
function el(tag, props = {}, ...children) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(props)) {
    if (value === undefined || value === null || value === false) continue;
    if (key === "class") node.className = value;
    else node.setAttribute(key, value === true ? "" : value);
  }
  for (const child of children.flat()) {
    if (child !== null && child !== undefined && child !== false) node.append(child);
  }
  return node;
}

async function api(path, body) {
  const options = body === undefined ? {} : {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
  };
  const response = await fetch(path, options);
  const data = response.status === 204 ? {} : await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(typeof data.detail === "string" ? data.detail : `Request failed (${response.status})`);
  return data;
}

// Privacy-respecting events: a name and, at most, an episode or asset ID. Never payment data.
function track(event, extra = {}) {
  fetch("/api/v3/events", {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ event, ...extra }), keepalive: true,
  }).catch(() => {});
}

const money = (m) => m ? `${m.currency === "EUR" ? "€" : m.currency + " "}${Number(m.value).toLocaleString("en-US", { maximumFractionDigits: 2 })}` : "—";
const time = (iso) => iso ? iso.replace("T", " ").replace(/:00Z$|Z$/, " UTC") : "—";

// Environment ---------------------------------------------------------------------

let allowExecute = false;
const configReady = api("/api/config").then((config) => {
  allowExecute = config.allow_execute;
  byId("public-banner").hidden = !config.public_demo;
  const pill = byId("env-pill");
  if (config.public_demo) { pill.dataset.mode = "public"; pill.textContent = "Public demo · sandbox calls off"; }
  else if (config.allow_execute) { pill.dataset.mode = "sandbox"; pill.textContent = "PayPal sandbox · on"; }
  else { pill.dataset.mode = "local"; pill.textContent = "PayPal sandbox · off"; }
}).catch(() => {});

// Decision Lab --------------------------------------------------------------------

const DEFAULT_EPISODE = "E-AUTH-004";
let currentEpisode = DEFAULT_EPISODE;
let lastReceipt = null;
let authorityLevels = [];
let lastAuthority = null;

const TRUST = { signed_policy: "authoritative", authenticated_mandate: "authoritative", approved_workflow: "trusted", external_content: "untrusted" };

function kv(label, value) { return el("div", { class: "kv" }, el("b", {}, label), el("span", {}, value)); }

function renderIdentity(ep) {
  const d = ep.delegation;
  const id = ep.identity;
  byId("identity-body").replaceChildren(
    kv("Agent", id.agent_id || "missing"),
    kv("Principal", `${ep.principal.principal_id} · ${ep.principal.organization}`),
    kv("Delegation", d ? `${d.delegation_id} · ${d.allowed_actions.join(", ")}` : "none"),
    kv("Execution authority", d ? (d.execution_authority ? "yes" : "no") : "—"),
    kv("Autonomous limit", d ? money(d.max_amount) : "—"),
    kv("Model / version", `${id.model} · ${id.model_version}`),
    kv("Prompt / toolset", `${id.prompt_version} · ${id.toolset_version}`),
    kv("Policy bundle", d ? d.policy_bundle : "—"),
    kv("Token", id.token ? `audience ${id.token.audience} · expires ${time(id.token.expires_at)}${id.token.sender_constrained ? " · sender-constrained" : ""}` : "none"),
    kv("Delegation expires", d ? time(d.expires_at) : "—"),
  );
}

function renderMandate(ep) {
  const sources = ep.sources.map((s) => el("div", { class: `source ${TRUST[s.kind]}` },
    el("span", { class: `trust ${TRUST[s.kind]}` }, `${s.priority} · ${TRUST[s.kind]}`),
    el("b", {}, s.label),
    s.excerpt ? el("p", {}, s.excerpt) : null,
    el("p", { class: "note" }, el("code", {}, s.content_hash.slice(0, 23) + "…"))));
  byId("mandate-body").replaceChildren(
    el("p", {}, el("b", {}, "Opening request: "), ep.mandate.request_text),
    el("dl", { class: "facts" },
      el("dt", {}, "Amount"), el("dd", {}, money(ep.mandate.amount)),
      el("dt", {}, "Beneficiary"), el("dd", {}, ep.beneficiary ? `${ep.beneficiary.name} · ${ep.beneficiary.account_masked}${ep.beneficiary.verified ? " · verified" : " · not verified"}` : "—"),
      el("dt", {}, "Method / deadline"), el("dd", {}, `${ep.mandate.payment_method || "—"} · ${ep.mandate.deadline_minutes ? ep.mandate.deadline_minutes + " min" : "—"}`),
      el("dt", {}, "Requested action"), el("dd", {}, el("code", {}, ep.mandate.requested_action))),
    el("p", { class: "note" }, "Instruction hierarchy: 1 signed policy → 2 authenticated mandate → 3 approved workflow → 4 external content, always untrusted data."),
    el("div", { class: "sources" }, sources));
}

function renderAttack(ep, receipt) {
  const external = ep.sources.filter((s) => s.kind === "external_content");
  const parts = external.map((s) => el("div", { class: "source untrusted" },
    el("span", { class: "trust untrusted" }, "untrusted"), el("b", {}, s.label),
    el("p", {}, s.excerpt || ""),
    s.hidden_excerpt ? el("p", {}, el("b", {}, s.hidden_excerpt)) : el("p", { class: "note" }, "No hidden content."),
    s.extracted && s.extracted.account_masked ? el("p", { class: "note" }, `Account named in this source: ${s.extracted.account_masked}`) : null));
  const ii = receipt.instruction_integrity;
  const verdict = ii.beneficiary_mutation || ii.amount_mutation
    ? el("div", { class: "alert" }, ii.beneficiary_mutation ? "BENEFICIARY MUTATION DETECTED" : "AMOUNT MUTATION DETECTED")
    : ii.prompt_injection
      ? el("div", { class: "alert" }, "Instructions aimed at the agent were found and quarantined as data. No payment fact changed.")
      : el("div", { class: "ok" }, "External content agrees with the mandate and master data.");
  byId("attack-body").replaceChildren(el("div", { class: "sources" }, parts), verdict,
    el("p", { class: "note" }, "External content cannot expand authority or override higher-level instructions."));
  track("attack_revealed", { episode_id: ep.episode_id });
}

function renderGates(receipt) {
  byId("gates-body").replaceChildren(...receipt.gates.map((g) => el("div", { class: `gate g-${g.status}` },
    el("div", { class: "gate-head" }, el("b", {}, `${g.order}. ${g.label}`), el("span", { class: `s s-${g.status}` }, g.status.replace("_", " ").toUpperCase())),
    el("p", {}, g.explanation),
    g.reason_codes.length ? el("div", {}, g.reason_codes.map((c) => el("code", {}, c))) : null,
    el("p", { class: "meta" }, `${g.evidence_ref} · ${g.policy_version}`))));
  track("gate_completed", { episode_id: currentEpisode });
}

function nextButtons(action) {
  const options = {
    STOP: [["E-AUTH-004-R", "Add the missing controls"], ["E-AUTH-004-C", "Corrected instruction (PAUSE)"]],
    PAUSE: [["E-AUTH-004-R", "Capture the approval and verify"]],
    PAY: [["E-NEUT-001", "Change the mandate: another provider wins"], ["E-EXEC-002", "Replay the payment"]],
    HANDOFF: [["E-AUTH-004", "Back to the attack"]],
  }[action] || [];
  return el("div", { class: "next" }, options.map(([id, label]) => {
    const b = el("button", { class: "btn small", type: "button" }, label);
    b.addEventListener("click", () => { track("variant_run", { episode_id: id }); runEpisode(id); });
    return b;
  }));
}

function renderOutcome(data, receipt) {
  const key = data.answer_key;
  const matches = key.action === receipt.selected_action && JSON.stringify(key.reason_codes) === JSON.stringify(receipt.reason_codes);
  const policy = data.policy_decision;
  byId("outcome-title").textContent = {
    STOP: "APB stops this payment", PAUSE: "APB pauses for a resolvable control",
    PAY: "Every gate passed: PAY", HANDOFF: "APB hands the decision off",
  }[receipt.selected_action];
  const exec = receipt.execution_reference;
  byId("outcome-body").replaceChildren(
    el("div", { class: "verdict" }, el("span", { class: `word w-${receipt.selected_action}` }, receipt.selected_action),
      el("div", {}, el("p", {}, receipt.gates.length && explanationFor(receipt)),
        el("p", { class: "note" }, `Security outcome: ${receipt.security_outcome}`))),
    receipt.reason_codes.length ? el("div", { class: "codes" }, receipt.reason_codes.map((c) => el("code", {}, c))) : null,
    el("dl", { class: "facts" },
      el("dt", {}, "PayPal called"), el("dd", {}, receipt.paypal_called
        ? (exec && exec.mode === "sandbox" ? `Yes, PayPal sandbox · ${exec.reference}` : `Adapter invoked, simulated: no network call on this demo · ${exec ? exec.reference : ""}`)
        : exec && exec.duplicate ? `No second call · original reference ${exec.reference} returned` : "No"),
      el("dt", {}, "Policy decision"), el("dd", {}, el("code", {}, JSON.stringify(policyView(policy)))),
      el("dt", {}, "Answer key"), el("dd", {}, `${key.action}${key.reason_codes.length ? " · " + key.reason_codes.join(", ") : ""} · ${matches ? "matches" : "DOES NOT MATCH"}`)),
    nextButtons(receipt.selected_action));
  track("decision_returned", { episode_id: currentEpisode });
}

function explanationFor(receipt) {
  return {
    STOP: "Nothing executes. Route scoring and PayPal are never reached.",
    PAUSE: "Nothing executes until the step-up approval, confirmation or fresh evidence arrives.",
    PAY: "Every gate passed and the selected route is PayPal sandbox, the best observed eligible route within the declared candidate set and quote window.",
    HANDOFF: receipt.selected_route ? `The best observed eligible route is ${receipt.selected_route}. The decision is handed off there; PayPal is not called.` : "The case cannot be safely resolved within the supported policy or evidence. PayPal is not called.",
  }[receipt.selected_action];
}

function policyView(policy) {
  const out = { effect: policy.effect, reason: policy.reason };
  if (policy.required_approvers) Object.assign(out, { required_approvers: policy.required_approvers, bound_fields: policy.bound_fields, expires_in_seconds: policy.expires_in_seconds });
  return out;
}

function renderRoutes(data, receipt) {
  const route = receipt.gates.find((g) => g.gate === "route");
  if (route.status === "not_reached") {
    byId("routes-body").replaceChildren(el("div", { class: "alert" }, "ROUTE SCORING NOT REACHED"),
      el("p", { class: "muted" }, "Economic value cannot compensate for invalid identity, authority, beneficiary or execution state."));
    return;
  }
  const scores = new Map(receipt.route_scores.map((s) => [s.route_id, s]));
  const excluded = new Map(receipt.excluded_routes.map((e) => [e.route_id, e.reason]));
  const quotes = new Map(data.episode.quotes.map((q) => [q.route_id, q]));
  const cards = data.episode.routes.map((r) => {
    const status = r.route_id === receipt.selected_route ? "selected" : excluded.has(r.route_id) ? "excluded" : "eligible";
    const q = quotes.get(r.route_id);
    return el("div", { class: `route ${status}` },
      el("div", { class: "gate-head" }, el("b", {}, r.label), el("span", { class: `tag ${status}` }, status.toUpperCase())),
      el("div", { class: "amount" }, q ? `Recipient gets ${money(q.recipient_amount)}` : "No quote"),
      el("p", { class: "note" }, `${q ? "Fee " + money(q.fee) + " · " : ""}${r.delivery_minutes >= 1440 ? (r.delivery_minutes / 1440) + " days" : r.delivery_minutes >= 60 ? (r.delivery_minutes / 60) + " hours" : "immediate"}${scores.has(r.route_id) ? " · DVS " + scores.get(r.route_id).dvs.toFixed(1) : ""}`),
      excluded.has(r.route_id) ? el("p", {}, el("code", {}, excluded.get(r.route_id))) : null);
  });
  const ei = receipt.execution_integrity;
  byId("routes-body").replaceChildren(el("div", { class: "routes" }, cards),
    el("dl", { class: "facts" },
      el("dt", {}, "Quote rechecked at commit"), el("dd", {}, ei.quote_rechecked ? "yes" : "no"),
      el("dt", {}, "Idempotency key"), el("dd", {}, el("code", {}, ei.idempotency_key || "—")),
      el("dt", {}, "Duplicate check"), el("dd", {}, ei.duplicate_check),
      el("dt", {}, "Candidate set"), el("dd", {}, receipt.candidate_set.join(", "))),
    el("p", { class: "note" }, "Best observed eligible route within the declared candidate set, profile, policy and quote window. Sponsorship never affects the score."));
}

function renderAuthorityRequest(profile) {
  lastAuthority = profile;
  highlightLadder();
  byId("authority-request-body").replaceChildren(
    el("dl", { class: "facts" },
      el("dt", {}, "Requested action"), el("dd", {}, el("code", {}, profile.requested_action)),
      el("dt", {}, "Requested level"), el("dd", {}, `Level ${profile.requested_level}`),
      el("dt", {}, "Approved level"), el("dd", {}, profile.approved_level < 0 ? "none (no delegation)" : `Level ${profile.approved_level}`),
      el("dt", {}, "Result"), el("dd", {}, profile.authority_gap ? "Authority gap detected" : "Within approved authority"),
      el("dt", {}, "Can it be increased?"), el("dd", {}, profile.increase_path),
      el("dt", {}, "Expiry / re-authorize by"), el("dd", {}, `${time(profile.expires_at)} / ${time(profile.reauthorize_by)}`)),
    profile.missing_controls.length ? el("div", {}, el("p", {}, el("b", {}, "Missing controls")), el("ul", {}, profile.missing_controls.map((c) => el("li", {}, c)))) : null,
    profile.evidence_required.length ? el("div", {}, el("p", {}, el("b", {}, "Evidence required")), el("ul", {}, profile.evidence_required.map((c) => el("li", {}, c)))) : null);
}

function renderReceipt(receipt, all) {
  lastReceipt = receipt;
  const json = JSON.stringify(receipt, null, 2);
  const copy = el("button", { class: "btn small", type: "button" }, "Copy JSON");
  copy.addEventListener("click", async () => {
    try { await navigator.clipboard.writeText(json); copy.textContent = "Copied"; } catch { copy.textContent = "Copy failed"; }
    setTimeout(() => { copy.textContent = "Copy JSON"; }, 1500);
  });
  const download = el("a", { class: "btn small", download: `${receipt.receipt_id}.json` }, "Download JSON");
  download.href = URL.createObjectURL(new Blob([json], { type: "application/json" }));
  download.addEventListener("click", () => track("receipt_downloaded", { episode_id: receipt.episode_id }));
  byId("receipt-body").replaceChildren(el("div", { class: "receipt-grid" },
    el("div", { class: "card" },
      el("dl", { class: "facts" },
        ...[["receipt_id", receipt.receipt_id], ["episode_id", receipt.episode_id], ["selected_action", receipt.selected_action],
          ["security_outcome", receipt.security_outcome], ["agent", receipt.agent.id || "missing"],
          ["requested / approved level", `${receipt.authority.requested_level} / ${receipt.authority.approved_level}`],
          ["approval", receipt.approval.status], ["policy bundle", receipt.policy.bundle_version],
          ["world / method", `${receipt.world_version} / ${receipt.method_version}`],
          ["beneficiary mutation", String(receipt.instruction_integrity.beneficiary_mutation)],
          ["paypal_called", String(receipt.paypal_called)], ["data_mode", receipt.data_mode],
          ["trace_manifest_hash", receipt.trace_manifest_hash.slice(0, 23) + "…"], ["receipt_hash", receipt.receipt_hash.slice(0, 23) + "…"],
        ].flatMap(([k, v]) => [el("dt", {}, k), el("dd", {}, el("code", {}, v))])),
      all.length > 1 ? el("p", { class: "note" }, `${all.length} attempts in this run; showing the last. The first carried the execution.`) : null,
      el("div", { class: "row" }, copy, download)),
    el("pre", { class: "json", tabindex: "0", "aria-label": "Raw Decision Receipt JSON" }, json)));
  track("receipt_viewed", { episode_id: receipt.episode_id });
}

function markScenario(id) {
  for (const b of document.querySelectorAll(".scenario")) b.setAttribute("aria-pressed", String(b.dataset.episode === id));
  byId("episode-select").value = id;
  byId("lab-episode-id").textContent = id;
}

async function preview(id) {
  currentEpisode = id;
  markScenario(id);
  try {
    const ep = await api(`/api/v3/episodes/${encodeURIComponent(id)}`);
    renderIdentity(ep);
    renderMandate(ep);
  } catch (error) { byId("lab-status").textContent = error.message; }
}

let running = false;
async function runEpisode(id) {
  if (running) return;
  running = true;
  currentEpisode = id;
  markScenario(id);
  byId("run-episode").disabled = true;
  byId("lab-status").textContent = `Running ${id}…`;
  track("episode_started", { episode_id: id });
  try {
    await configReady;
    const data = await api(`/api/v3/episodes/${encodeURIComponent(id)}/run`, {});
    const receipt = data.receipts[data.receipts.length - 1];
    renderIdentity(data.episode);
    renderMandate(data.episode);
    renderAttack(data.episode, receipt);
    renderGates(receipt);
    renderOutcome(data, receipt);
    renderRoutes(data, receipt);
    renderAuthorityRequest(data.authority);
    renderReceipt(receipt, data.receipts);
    if (receipt.paypal_called && data.execution_mode === "sandbox") track("paypal_sandbox_executed", { episode_id: id });
    byId("lab-status").textContent = `${id}: ${receipt.selected_action}. Decision Receipt ${receipt.receipt_id}.`;
  } catch (error) {
    byId("lab-status").textContent = error.message;
  } finally {
    running = false;
    byId("run-episode").disabled = false;
  }
}

// Authority ladder ---------------------------------------------------------------

function showLevel(level) {
  const item = authorityLevels[level];
  for (const b of document.querySelectorAll(".level")) b.setAttribute("aria-pressed", String(Number(b.dataset.level) === level));
  byId("level-detail").replaceChildren(
    el("h3", {}, `Level ${item.level} · ${item.name}`),
    el("dl", { class: "facts" },
      el("dt", {}, "Capability"), el("dd", {}, item.capability),
      el("dt", {}, "Exposure"), el("dd", {}, item.exposure),
      el("dt", {}, "Authority decision"), el("dd", {}, item.decision),
      el("dt", {}, "Actions"), el("dd", {}, item.actions.join(", "))),
    el("p", {}, el("b", {}, "Minimum controls")), el("ul", {}, item.controls.map((c) => el("li", {}, c))),
    el("p", {}, el("b", {}, "Evidence required")), el("ul", {}, item.evidence.map((c) => el("li", {}, c))));
  track("authority_level_viewed");
}

function highlightLadder() {
  for (const b of document.querySelectorAll(".level")) {
    const n = Number(b.dataset.level);
    b.classList.toggle("requested", Boolean(lastAuthority) && n === lastAuthority.requested_level);
    b.classList.toggle("approved", Boolean(lastAuthority) && n === lastAuthority.approved_level);
    const note = b.querySelector("small");
    note.textContent = !lastAuthority ? "" : n === lastAuthority.requested_level ? "requested" : n === lastAuthority.approved_level ? "approved" : "";
  }
}

async function loadAuthority() {
  const data = await api("/api/v3/authority");
  authorityLevels = data.levels;
  byId("ladder").replaceChildren(...data.levels.map((item) => {
    const b = el("button", { class: "level", type: "button", "data-level": String(item.level), "aria-pressed": "false" },
      el("b", {}, String(item.level)), el("span", {}, item.name), el("small", {}, ""));
    b.addEventListener("click", () => showLevel(item.level));
    return b;
  }));
  showLevel(4);
  highlightLadder();
}

// World, monitoring, episodes ----------------------------------------------------

const WORLD_METRICS = [
  ["correct_action_rate", "Correct action"], ["unsafe_execution_rate", "Unsafe execution"],
  ["wrong_refusal_rate", "Wrong refusal"], ["evidence_completeness", "Evidence completeness"],
  ["prompt_injection_success_rate", "Prompt-injection success"], ["beneficiary_mutation_detection_rate", "Mutation detection"],
  ["replay_block_rate", "Replay blocked"], ["pass_5", "pass^5"],
];
const pct = (v) => v === null || v === undefined ? "n/a" : `${Math.round(v * 1000) / 10}%`;

async function loadWorld() {
  try {
    const data = await api("/api/v3/world");
    byId("world-version").textContent = data.world_version;
    const head = el("tr", {}, el("th", { scope: "col" }, "Metric"), data.agents.map((a) => el("th", { scope: "col", class: "num" }, a.agent)));
    const rows = WORLD_METRICS.map(([key, label]) => el("tr", {}, el("th", { scope: "row", title: data.metric_definitions[key] }, label),
      data.agents.map((a) => el("td", { class: "num" }, key === "pass_5" ? a.metrics[key].toFixed(3) : pct(a.metrics[key])))));
    byId("world-body").replaceChildren(
      el("div", { class: "table-wrap" }, el("table", {}, el("caption", { class: "note" }, `${data.episodes.length} synthetic episodes · ${data.runs_per_episode} runs each · method ${data.method_version} · policy ${data.policy_bundle}`), el("thead", {}, head), el("tbody", {}, rows))),
      el("p", { class: "note" }, data.note));
  } catch (error) { byId("world-body").textContent = error.message; }
}

async function loadMonitoring() {
  const data = await api("/api/v3/monitoring");
  byId("monitoring-body").replaceChildren(
    el("div", { class: "table-wrap" }, el("table", {},
      el("thead", {}, el("tr", {}, ["Signal", "Now", "Threshold", "Automated action", "State"].map((h) => el("th", { scope: "col" }, h)))),
      el("tbody", {}, data.signals.map((s) => el("tr", {}, el("td", {}, s.signal), el("td", {}, String(s.value)), el("td", {}, s.threshold), el("td", {}, s.action),
        el("td", {}, el("span", { class: `s ${s.state === "tripped" ? "s-fail" : "s-pass"}` }, s.state === "tripped" ? "TRIPPED" : "OK"))))))),
    el("p", { class: "note" }, `${data.window_label}. ${data.note}`));
}

async function loadEpisodes() {
  const { episodes } = await api("/api/v3/episodes");
  byId("episode-select").replaceChildren(...episodes.map((e) => el("option", { value: e.episode_id }, `${e.episode_id} · ${e.title}`)));
  byId("episode-list").replaceChildren(...episodes.map((e) => {
    const b = el("button", { class: "btn small", type: "button" }, "Run in the Decision Lab");
    b.addEventListener("click", () => { byId("decision-lab").scrollIntoView(); runEpisode(e.episode_id); });
    return el("article", { class: "card episode" }, el("code", {}, e.episode_id), el("b", {}, e.title),
      el("span", { class: "note" }, e.family), el("p", {}, e.summary), e.relaxes ? el("p", { class: "note" }, `Relaxes: ${e.relaxes}`) : null, b);
  }));
  byId("episode-select").value = currentEpisode;
}

// Video ---------------------------------------------------------------------------

function wireVideo() {
  const video = byId("demo-video");
  const sent = new Set();
  const once = (name) => { if (!sent.has(name)) { sent.add(name); track(name, { asset_id: "apb_v2_product_demo" }); } };
  video.addEventListener("play", () => once("video_play"));
  video.addEventListener("timeupdate", () => {
    if (!video.duration) return;
    const p = video.currentTime / video.duration;
    if (p >= 0.25) once("video_progress_25");
    if (p >= 0.5) once("video_progress_50");
    if (p >= 0.75) once("video_progress_75");
  });
  video.addEventListener("ended", () => { once("video_complete"); byId("video-after").hidden = false; byId("video-to-lab").focus(); });
  byId("video-to-lab").addEventListener("click", () => { track("interactive_demo_click", { asset_id: "apb_v2_product_demo" }); byId("decision-lab").scrollIntoView(); runEpisode(DEFAULT_EPISODE); });
}

// Navigation ----------------------------------------------------------------------

const PATHS = { "/decision-lab": "decision-lab", "/authority": "authority", "/episodes": "episodes", "/world": "world", "/receipt": "receipt", "/demo": "demo", "/economic-quality": "economic-quality" };
const navLinks = [...document.querySelectorAll(".nav a")];
const spy = new IntersectionObserver((entries) => {
  for (const entry of entries) {
    if (!entry.isIntersecting) continue;
    for (const link of navLinks) {
      if (link.getAttribute("href") === `#${entry.target.id}`) link.setAttribute("aria-current", "true");
      else link.removeAttribute("aria-current");
    }
  }
}, { rootMargin: "-40% 0px -55% 0px" });
document.querySelectorAll("main > section").forEach((s) => spy.observe(s));
for (const link of document.querySelectorAll("[data-event]")) link.addEventListener("click", () => track(link.dataset.event, { asset_id: "apb_v2_product_demo" }));

// v0.1 economic-quality lab --------------------------------------------------------

const missing = (v) => v === null || v === undefined;
const whole = (v) => missing(v) ? "not disclosed" : Number(v).toLocaleString("en-US");
function amount(currency, value) {
  if (missing(value)) return "not disclosed";
  const n = Number(value);
  const digits = currency !== "USD" && Number.isInteger(n) ? 0 : 2;
  return `${currency} ${n.toLocaleString("en-US", { minimumFractionDigits: digits, maximumFractionDigits: 2 })}`;
}
const SOURCE_BADGES = { ai: "ai", rules: "rule-based parser, no AI", fixture: "fixture" };
let decisionId = null;

function setBusy(busy) { for (const id of ["run-text", "run-a", "run-b", "run-c", "run-d", "run-e"]) byId(id).disabled = busy; }

async function runDecision(body) {
  setBusy(true);
  decisionId = null;
  byId("status").textContent = "Deciding…";
  byId("result").replaceChildren();
  try {
    const data = await api("/api/decide", body);
    await configReady;
    if (data.status === "ok") { decisionId = data.decision_id; renderDecision(data); }
    else {
      byId("result").replaceChildren(el("div", { class: "card" }, el("b", {}, data.status === "missing_fields" ? "More information is needed" : data.status === "out_of_scope" ? "Out of scope" : "AI is not available"),
        data.missing_fields && data.missing_fields.length ? el("ul", {}, data.missing_fields.map((m) => el("li", {}, m.question))) : el("p", {}, data.message)));
    }
    byId("status").textContent = "";
  } catch (error) { byId("status").textContent = error.message; }
  finally { setBusy(false); }
}

function renderDecision(data) {
  const { receipt, mandate, request, receive_currency: cur } = data;
  const reasons = new Map(receipt.excluded_routes.map((r) => [r.provider, r.reason]));
  const cards = receipt.candidate_set.map((q) => {
    const status = q.provider === receipt.selected_route ? "selected" : reasons.has(q.provider) ? "excluded" : "eligible";
    return el("div", { class: `route ${status}` },
      el("div", { class: "gate-head" }, el("b", {}, q.provider), el("span", { class: `tag ${status}` }, status.toUpperCase())),
      el("div", { class: "amount" }, amount(cur, q.recipient_amount)),
      el("p", { class: "note" }, `fee ${amount(mandate.send_currency, q.fee)} · ${whole(q.delivery_minutes)} min${receipt.scores[q.provider] !== undefined ? " · DVS " + receipt.scores[q.provider].toFixed(1) : ""}`),
      reasons.has(q.provider) ? el("p", {}, el("code", {}, reasons.get(q.provider))) : null);
  });
  const parts = [
    el("div", { class: "card" }, el("div", { class: "verdict" }, el("span", { class: `word w-${receipt.decision}` }, receipt.decision),
      el("div", {}, el("p", {}, receipt.stop_reason || receipt.confirmation_reason || `Selected route: ${receipt.selected_route}`),
        el("p", { class: "note" }, `${SOURCE_BADGES[request.source]} · ${amount(request.currency, request.amount)} · ${request.payout_method} · ${request.wallet} · ${whole(request.deadline_minutes)} min · policy ${receipt.policy_version}`)))),
    el("div", { class: "legacy-cards" }, cards),
    el("div", { class: "card" }, el("p", {}, receipt.explanation), el("p", { class: "note" }, receipt.explanation_source === "ai" ? "AI, grounding checked" : "Template")),
  ];
  if (allowExecute && !receipt.stop_reason) parts.push(executePanel());
  parts.push(el("details", { class: "card" }, el("summary", {}, "Decision receipt (v0.1)"), el("pre", { class: "json", tabindex: "0" }, JSON.stringify(receipt, null, 2))));
  byId("result").replaceChildren(...parts);
}

function executePanel() {
  const button = el("button", { class: "btn primary", type: "button" }, "Execute in sandbox");
  const output = el("div", { role: "status" });
  async function send(confirmPayee) {
    button.disabled = true;
    output.textContent = "Sending to PayPal sandbox…";
    try {
      const { execution } = await api("/api/execute", { decision_id: decisionId, confirm_payee: confirmPayee });
      output.replaceChildren(el("pre", { class: "json" }, JSON.stringify(execution, null, 2)));
      if (execution.status === "needs_confirmation") {
        const confirm = el("button", { class: "btn primary", id: "confirm-payee", type: "button" }, "Confirm payee and execute");
        confirm.addEventListener("click", () => send(true));
        output.append(confirm);
      }
    } catch (error) { output.textContent = error.message; }
    button.disabled = false;
  }
  button.addEventListener("click", () => send(false));
  return el("div", { class: "card" }, el("h3", {}, "Execution"), button, output);
}

let benchmarkLoaded = false;
async function loadBenchmark() {
  if (benchmarkLoaded) return;
  try {
    const data = await api("/api/benchmark");
    const head = el("tr", {}, ["Test", "Agent", "Chosen route", "Compliant", "Score"].map((h) => el("th", { scope: "col" }, h)));
    const blocks = data.corridors.map((c) => el("details", { class: "card" }, el("summary", {}, `${c.label} (${c.currency})`),
      el("div", { class: "table-wrap" }, el("table", {}, el("thead", {}, head.cloneNode(true)), el("tbody", {},
        data.scenarios.flatMap((s, i) => c.agents.map((a) => el("tr", {}, el("td", {}, s.name), el("td", {}, a.agent),
          el("td", {}, el("code", {}, a.scenarios[i].chosen_route ?? "no payment")), el("td", {}, a.scenarios[i].mandate_compliant ? "Yes" : "No"),
          el("td", { class: "num" }, a.scenarios[i].score.toFixed(1))))))))));
    byId("benchmark").replaceChildren(
      el("div", { class: "legacy-cards" }, data.agents.map((a) => el("div", { class: "card" }, el("div", { class: "amount" }, a.apb_score.toFixed(1)),
        el("p", {}, `${a.agent}: ${a.tests_passed} of ${a.tests_total} tests, $${a.total_usd_lost.toFixed(2)} lost`)))),
      ...blocks, el("p", { class: "note" }, `Method ${data.method_version}, ${data.data_mode} data. `, el("a", { href: "/docs/METHOD.md" }, "Read the v0.1 method")));
    benchmarkLoaded = true;
  } catch (error) { byId("benchmark").textContent = error.message; }
}

function selectTab(name) {
  for (const tab of ["decide", "benchmark"]) {
    byId(`tab-${tab}`).setAttribute("aria-selected", String(tab === name));
    byId(`view-${tab}`).hidden = tab !== name;
  }
  if (name === "benchmark") loadBenchmark();
}

// Wiring --------------------------------------------------------------------------

for (const b of document.querySelectorAll(".scenario")) b.addEventListener("click", () => runEpisode(b.dataset.episode));
byId("episode-select").addEventListener("change", (e) => preview(e.target.value));
byId("run-episode").addEventListener("click", () => runEpisode(byId("episode-select").value));
for (const link of document.querySelectorAll("[data-run-default]")) link.addEventListener("click", () => runEpisode(DEFAULT_EPISODE));
byId("tab-decide").addEventListener("click", () => selectTab("decide"));
byId("tab-benchmark").addEventListener("click", () => selectTab("benchmark"));
for (const key of ["a", "b", "c", "d", "e"]) byId(`run-${key}`).addEventListener("click", () => runDecision({ case: key }));
byId("run-text").addEventListener("click", () => {
  const text = byId("request").value.trim();
  if (text) runDecision({ text }); else byId("status").textContent = "Enter a payment request, or pick a case.";
});

wireVideo();
loadEpisodes().then(() => preview(DEFAULT_EPISODE)).catch(() => {});
loadAuthority().catch(() => {});
loadWorld();
loadMonitoring().catch(() => {});
const target = PATHS[window.location.pathname];
const shared = new URLSearchParams(window.location.search).get("episode");
if (shared && /^E-[A-Z0-9-]{3,16}$/.test(shared)) {
  // A shared link to one scenario: /decision-lab?episode=E-AUTH-004-R
  byId("decision-lab").scrollIntoView();
  runEpisode(shared).then(() => { if (target && target !== "decision-lab") byId(target).scrollIntoView(); });
} else if (target) requestAnimationFrame(() => byId(target).scrollIntoView());
