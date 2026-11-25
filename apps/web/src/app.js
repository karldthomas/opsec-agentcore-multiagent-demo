const SESSION_KEY = "opsfusion.cognito.session";

function loadSession() {
  try {
    const raw = window.localStorage.getItem(SESSION_KEY);
    const session = raw ? JSON.parse(raw) : null;
    return session?.id_token ? session : null;
  } catch {
    return null;
  }
}

function saveSession(session) {
  try {
    if (session) window.localStorage.setItem(SESSION_KEY, JSON.stringify(session));
    else window.localStorage.removeItem(SESSION_KEY);
  } catch {
    // A browser with storage disabled can still use the current page session.
  }
}

const query = new URLSearchParams(window.location.search);
const state = {
  runId: query.get("run_id") || null,
  data: null,
  view: "overview",
  selectedAgentId: null,
  busy: null,
  workflowExpanded: false,
  notice: "",
  noticeTone: "",
  investigateInFlight: false,
  followUpInFlight: false,
  followUpError: "",
  followUpStartedAt: null,
  followUpInitialToolCount: 0,
  followUpAutoKey: "",
  followUpAutoAttempts: 0,
  progressTimer: null,
  pollTimer: null,
  refreshing: false,
  lastRenderSignature: "",
  lastRefresh: null,
  session: loadSession(),
  principal: null,
  authLoading: true,
  authChallenge: null,
  authError: "",
};

const $ = (id) => document.getElementById(id);

const STAGES = [
  { id: "initialize", number: "01", label: "Start investigation", detail: "scope the incident" },
  { id: "parallel_investigation", number: "02", label: "Gather evidence", detail: "service, changes, customer impact" },
  { id: "reconcile_evidence", number: "03", label: "Confirm likely cause", detail: "connect facts to cause" },
  { id: "awaiting_follow_up", number: "04", label: "Close evidence gaps", detail: "run read-only checks" },
  { id: "completed", number: "05", label: "Analysis complete", detail: "findings ready for review" },
];

const FOLLOW_UP_RETRY_BASE_DELAY_MS = 1000;
const FOLLOW_UP_RETRY_MAX_DELAY_MS = 30000;

const AGENTS = [
  { id: "orchestrator", label: "Incident orchestrator", short: "ORCH", purpose: "delegates and reconciles", skill: "investigation-planning · evidence-reconciliation", tools: ["get_incident_context", "get_shared_evidence", "search_runbooks"], accent: "violet" },
  { id: "telemetry-agent", label: "Telemetry agent", short: "TEL", purpose: "metrics, logs, traces, alarms", skill: "telemetry-triage", tools: ["get_incident_context", "query_cloudwatch_metrics", "search_application_logs", "get_service_traces", "get_active_alarms", "compare_metric_windows", "get_service_health"], accent: "cyan" },
  { id: "change-agent", label: "Change analysis agent", short: "CHG", purpose: "deployments, config, runbooks", skill: "change-analysis", tools: ["get_incident_context", "list_recent_deployments", "get_deployment_details", "compare_deployment_versions", "get_configuration_changes", "search_runbooks"], accent: "blue" },
  { id: "data-agent", label: "Data + impact agent", short: "DATA", purpose: "database, queue, orders", skill: "business-impact-analysis", tools: ["get_incident_context", "get_database_metrics", "get_queue_metrics", "query_order_records", "get_order_processing_history", "calculate_customer_impact"], accent: "amber" },
];

const SCENARIO_DETAILS = {
  "INC-2048": {
    title: "Order processing degradation",
    summary: "A recent orders-service deployment was followed by elevated API errors, database connection pressure, a growing processing queue, and delayed orders.",
    facts: ["18.7% API errors", "42,800 queued", "816 customers affected"],
    analysis: "The analysis correlates the release, service signals, database pressure, and customer impact to establish the likely cause.",
  },
  "INC-2051": {
    title: "Retry storm",
    summary: "Repeated timeout errors are amplifying request load while orders-service falls behind on processing customer work.",
    facts: ["repeated timeouts", "request amplification", "customer delay"],
    analysis: "The analysis correlates timeout and retry observations with customer-visible processing delay.",
  },
  "INC-2054": {
    title: "Database query regression",
    summary: "A recent application release coincides with slow database queries, elevated latency, and delayed order processing.",
    facts: ["slow queries", "elevated latency", "delayed orders"],
    analysis: "The analysis compares the release and database signals to distinguish a query regression from other causes.",
  },
  "INC-2057": {
    title: "Downstream dependency outage",
    summary: "Requests to a downstream dependency are timing out while customer processing degrades and work accumulates.",
    facts: ["dependency timeouts", "request pressure", "customer impact"],
    analysis: "The analysis correlates dependency timeouts, request pressure, and accumulated customer work.",
  },
};

const DEFAULT_SCENARIO_DETAIL = {
  title: "Synthetic service incident",
  summary: "The investigation will collect observable service, change, and customer-impact evidence for this synthetic incident.",
  facts: ["read-only evidence", "cause analysis", "customer impact"],
  analysis: "The console stops after evidence-grounded findings. It never changes simulator state.",
};

const EMPTY_EVIDENCE = { evidence: [], findings: [], hypotheses: [], unresolved_questions: [], tool_events: [], observations: [] };

function clearSession() {
  state.session = null;
  saveSession(null);
}

function expireSession(message = "Your session expired. Sign in again to continue.") {
  clearSession();
  state.principal = null;
  state.authChallenge = null;
  state.authLoading = false;
  state.authError = message;
  state.data = null;
  state.runId = null;
  state.lastRenderSignature = "";
  state.selectedAgentId = null;
  state.followUpInFlight = false;
  state.followUpError = "";
  state.followUpStartedAt = null;
  state.followUpAutoKey = "";
  state.followUpAutoAttempts = 0;
  stopProgressTimer();
  state.notice = "";
  if (state.pollTimer) window.clearInterval(state.pollTimer);
  state.pollTimer = null;
  renderAuthShell();
}

async function api(path, options = {}, includeSession = true) {
  const headers = { "Content-Type": "application/json", ...(options.headers || {}) };
  if (includeSession && state.session?.id_token) {
    headers.Authorization = `Bearer ${state.session.id_token}`;
  }
  const response = await fetch(path, {
    ...options,
    headers,
  });
  const raw = await response.text();
  let payload = {};
  try { payload = raw ? JSON.parse(raw) : {}; } catch { payload = { detail: raw }; }
  if (!response.ok) {
    const detail = payload.detail;
    const message = typeof detail === "string" ? detail : detail?.message;
    const error = new Error(message || `Request failed (${response.status})`);
    error.status = response.status;
    error.payload = payload;
    if (response.status === 401 && includeSession && state.session && !path.startsWith("/auth/")) {
      expireSession();
    }
    throw error;
  }
  return payload;
}

function escapeHtml(value) {
  return String(value ?? "").replace(/[&<>"']/g, (character) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#039;",
  }[character]));
}

function safeJson(value) {
  try { return JSON.stringify(value, null, 2) || "{}"; } catch { return String(value); }
}

function renderSignature(data) {
  const cp = checkpoint(data);
  const ev = evidence(data);
  const tasks = Object.values(cp.task_results || {}).map((task) => {
    const session = task?.session || {};
    return [
      task?.task_id,
      task?.role || session.role,
      task?.status,
      session.session_id,
      session.event_count,
      session.skill_trace?.status,
      Boolean(session.prompt_trace),
    ].join(":");
  });
  return JSON.stringify({
    run: [data?.run?.run_id, data?.run?.status, data?.run?.simulation_tick],
    checkpoint: [cp.version, cp.stage, cp.retry_count, (cp.completed_stages || []).join(",")],
    tasks,
    evidence: [
      ev.evidence?.length || 0,
      ev.findings?.length || 0,
      ev.hypotheses?.length || 0,
      ev.tool_events?.length || 0,
      ev.observations?.length || 0,
    ],
    health: [
      data?.health?.status,
      data?.health?.api_error_rate,
      data?.health?.queue_depth,
      data?.health?.database_connections,
    ],
  });
}

function jsonDetails(label, value, open = false) {
  return `<details class="data-details"${open ? " open" : ""}><summary>${escapeHtml(label)}</summary><pre>${escapeHtml(safeJson(value))}</pre></details>`;
}

function pretty(value) {
  return String(value ?? "")
    .replaceAll("_", " ")
    .replace(/\b\w/g, (character) => character.toUpperCase());
}

function renderScenarioDescription() {
  const target = $("scenario-description");
  if (!target) return;
  const scenarioId = $("scenario")?.value || "INC-2048";
  const detail = SCENARIO_DETAILS[scenarioId] || DEFAULT_SCENARIO_DETAIL;
  target.innerHTML = `<div class="scenario-description-heading"><div><p class="eyebrow">SCENARIO BRIEF</p><strong>${escapeHtml(detail.title)}</strong></div><span class="scenario-description-id">${escapeHtml(scenarioId)}</span></div><p>${escapeHtml(detail.summary)}</p><div class="scenario-description-facts">${detail.facts.map((fact) => `<span>${escapeHtml(fact)}</span>`).join("")}</div><small>${escapeHtml(detail.analysis)}</small>`;
}

function displayToolName(value) {
  const name = String(value || "tool").replace(/^.*___/, "").replaceAll("_", " ").trim();
  return name ? name.replace(/\b\w/g, (character) => character.toUpperCase()) : "Tool call";
}

function formatNumber(value, suffix = "") {
  if (value === undefined || value === null || value === "") return "—";
  const numeric = Number(value);
  if (!Number.isFinite(numeric)) return `${escapeHtml(value)}${suffix}`;
  return `${numeric.toLocaleString(undefined, { maximumFractionDigits: numeric % 1 ? 1 : 0 })}${suffix}`;
}

function formatTime(value) {
  if (!value) return "—";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? String(value) : date.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" });
}

function formatDateTime(value) {
  if (!value) return "—";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? String(value) : date.toLocaleString([], { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit", second: "2-digit" });
}

function elapsedSince(value) {
  const timestamp = new Date(value || 0).getTime();
  if (!Number.isFinite(timestamp) || timestamp <= 0) return "an unknown amount of time";
  const seconds = Math.max(0, Math.floor((Date.now() - timestamp) / 1000));
  if (seconds < 60) return `${seconds} second${seconds === 1 ? "" : "s"}`;
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return `${minutes} minute${minutes === 1 ? "" : "s"}`;
  const hours = Math.floor(minutes / 60);
  return `${hours} hour${hours === 1 ? "" : "s"}`;
}

function stopProgressTimer() {
  if (state.progressTimer) window.clearInterval(state.progressTimer);
  state.progressTimer = null;
}

function startProgressTimer() {
  stopProgressTimer();
  state.progressTimer = window.setInterval(() => {
    if (state.busy !== "follow-up" || document.hidden) {
      stopProgressTimer();
      return;
    }
    const target = document.querySelector("[data-follow-up-runtime]");
    if (target) target.textContent = `Running for ${elapsedSince(state.followUpStartedAt)}`;
  }, 1000);
}

function followUpRequestKey(data) {
  const requests = checkpoint(data).metadata?.follow_up_requests || [];
  return `${state.runId || ""}:${safeJson(requests)}`;
}

function maybeRunAutomaticFollowUp(data) {
  if (document.hidden) return;
  const cp = checkpoint(data);
  if (cp.stage !== "awaiting_follow_up") {
    state.followUpAutoKey = "";
    state.followUpAutoAttempts = 0;
    return;
  }
  if (state.investigateInFlight || state.followUpInFlight || state.busy) return;
  const key = followUpRequestKey(data);
  if (key === state.followUpAutoKey) return;
  state.followUpAutoKey = key;
  state.followUpAutoAttempts += 1;
  const runId = state.runId;
  window.setTimeout(() => {
    if (document.hidden || state.runId !== runId || checkpoint(state.data).stage !== "awaiting_follow_up") return;
    runFollowUp(true);
  }, 0);
}

function shortId(value, size = 24) {
  const text = String(value || "");
  return text.length > size ? `${text.slice(0, Math.max(8, size - 5))}…${text.slice(-4)}` : text;
}

function toneForStatus(status) {
  const value = String(status || "").toLowerCase();
  if (["healthy", "resolved", "completed", "complete", "ok", "passed"].some((item) => value.includes(item))) return "good";
  if (["failed", "error", "rejected", "expired"].some((item) => value.includes(item))) return "bad";
  if (["awaiting", "investigating", "active", "pending", "running"].some((item) => value.includes(item))) return "warn";
  return "neutral";
}

function statusPill(label, tone = "neutral", dot = true) {
  return `<span class="status-pill ${tone}">${dot ? '<span class="status-dot"></span>' : ""}${escapeHtml(label)}</span>`;
}

function stageLabel(id) {
  return STAGES.find((stage) => stage.id === id)?.label || (id ? "Analysis complete" : "Not started");
}

function isAnalysisCompleteStage(stage) {
  return stage === "completed" || (Boolean(stage) && !STAGES.some((item) => item.id === stage));
}

function stageStatusLabel(status) {
  return {
    complete: "Completed",
    active: "In progress",
    attention: "Needs evidence",
    pending: "Pending",
  }[status] || "Pending";
}

function workflowNextStep(stage) {
  return {
    initialize: "Set the incident scope and begin checking service health, recent changes, and customer impact.",
    parallel_investigation: "The investigation team is checking service health, recent changes, and customer impact in parallel.",
    reconcile_evidence: "The team is comparing the available facts to identify the most likely cause.",
    awaiting_follow_up: "Run the read-only checks that close the evidence gap before analysis completes.",
    completed: "The read-only analysis is complete and ready for review.",
  }[stage] || "The investigation is waiting for its next step.";
}

function agentLabel(id) {
  return AGENTS.find((agent) => agent.id === id)?.label || pretty(id || "specialist");
}

function followUpReasons(data) {
  const metadata = checkpoint(data).metadata || {};
  const requests = Array.isArray(metadata.follow_up_requests) ? metadata.follow_up_requests : [];
  const requestReasons = [];
  const seen = new Set();
  const addReason = (text) => {
    const value = String(text || "").trim();
    const key = value.toLowerCase();
    if (value && !seen.has(key)) {
      seen.add(key);
      requestReasons.push({ label: "Decision question", text: value });
    }
  };
  requests.forEach((request) => {
    const item = typeof request === "string" ? { objective: request } : request || {};
    const rawText = item.objective || item.question || item.reason || item.description;
    const normalized = String(rawText || "").toLowerCase();
    const text = normalized.includes("rollback") || normalized.includes("deployment")
      ? "Which deployment change is most closely associated with the observed impact?"
      : normalized.includes("connection pool") || normalized.includes("queue") || normalized.includes("backlog") || normalized.includes("capacity") || normalized.includes("scaling")
        ? "How do database and queue pressure explain the customer impact?"
        : normalized.includes("resource") || normalized.includes("service health") || normalized.includes("under pressure") || normalized.includes("correlate")
          ? "Which service signals establish the timing and severity of the incident?"
          : normalized.includes("maintenance") || normalized.includes("auto-scaling")
            ? "What observable configuration facts remain unconfirmed?"
            : rawText;
    addReason(text);
  });
  if (requestReasons.length) return requestReasons;

  const unresolved = (evidence(data).unresolved_questions || []).map((item) => (
    typeof item === "string" ? item : item?.question || item?.statement || ""
  )).filter(Boolean);
  unresolved.forEach(addReason);
  if (requestReasons.length) return requestReasons;

  const findings = evidence(data).findings || [];
  if (!findings.length) {
    return [{
      label: "Analysis question",
      text: "What evidence is still needed to explain the incident and its customer impact?",
    }];
  }
  return [{
    label: "Analysis question",
    text: "Which causal explanation best fits the evidence collected so far?",
  }];
}

function checkpoint(data) { return data?.checkpoint || {}; }
function evidence(data) { return data?.evidence || EMPTY_EVIDENCE; }

function stageState(id, data) {
  const current = checkpoint(data).stage || "initialize";
  const completed = checkpoint(data).completed_stages || [];
  if (id === "completed" && isAnalysisCompleteStage(current)) return "complete";
  if (id === "awaiting_follow_up" && completed.includes("follow_up_investigation")) return "complete";
  if (completed.includes(id)) return "complete";
  if (current === id) return isEvidenceStalled(data) ? "attention" : "active";
  if (current === "awaiting_follow_up" && id === "awaiting_follow_up") return "attention";
  return "pending";
}

function orderedToolEvents(data) {
  return [...(evidence(data).tool_events || [])].sort((left, right) => {
    const a = new Date(left.observed_at || 0).getTime();
    const b = new Date(right.observed_at || 0).getTime();
    return (Number.isFinite(a) ? a : 0) - (Number.isFinite(b) ? b : 0);
  });
}

function observationMap(data) {
  return new Map((evidence(data).observations || []).map((item) => [item.evidence_id, item]));
}

function compactObservationValue(value) {
  if (value === null || value === undefined || value === "") return "—";
  if (Array.isArray(value)) return value.slice(0, 3).map((item) => compactObservationValue(item)).join(", ");
  if (typeof value === "object") return Object.entries(value).slice(0, 3).map(([key, item]) => `${pretty(key)} ${compactObservationValue(item)}`).join(" · ");
  if (typeof value === "number") return formatNumber(value);
  return String(value);
}

function observationFacts(record) {
  if (!record || typeof record !== "object") return [];
  const metadata = new Set(["record_id", "timestamp", "observed_at", "tenant_id", "sample_order_ids"]);
  return Object.entries(record)
    .filter(([key]) => !metadata.has(key))
    .slice(0, 6)
    .map(([key, value]) => `${pretty(key)}: ${compactObservationValue(value)}`);
}

function responseFacts(event, data) {
  const facts = [];
  const addRecord = (record) => {
    observationFacts(record).forEach((fact) => {
      if (!facts.includes(fact)) facts.push(fact);
    });
  };
  const payload = event?.data;
  if (payload && typeof payload === "object") {
    for (const value of Object.values(payload)) {
      if (Array.isArray(value)) value.filter((item) => item && typeof item === "object").slice(0, 4).forEach(addRecord);
      else if (value && typeof value === "object") addRecord(value);
    }
    for (const [key, value] of Object.entries(payload)) {
      if (typeof value !== "object") facts.push(`${pretty(key)}: ${compactObservationValue(value)}`);
    }
  }
  const observations = observationMap(data);
  for (const evidenceId of event?.evidence_ids || []) addRecord(observations.get(evidenceId)?.record);
  if (!facts.length && event?.tool_name?.includes("service_health")) addRecord(data?.health);
  return [...new Set(facts)].slice(0, 7);
}

function renderLiveEvidenceUpdates(data) {
  const ev = evidence(data);
  const events = orderedToolEvents(data).slice(-6).reverse();
  const findings = (ev.findings || []).slice(-3).reverse();
  const updates = events.map((event) => {
    const facts = responseFacts(event, data);
    const rawResponse = event.data || (event.errors?.length ? { errors: event.errors } : null);
    return `<article class="live-update-row ${toneForStatus(event.status)}"><div class="live-update-heading"><strong>${escapeHtml(businessToolLabel(event.tool_name))}</strong><time>${escapeHtml(formatTime(event.observed_at))}</time></div><small>${escapeHtml(event.status === "ok" ? "The check returned this information" : pretty(event.status || "check failed"))}</small>${facts.length ? `<div class="live-update-facts">${facts.map((fact) => `<span>${escapeHtml(fact)}</span>`).join("")}</div>` : ""}${rawResponse ? jsonDetails("View full response", rawResponse, false) : ""}</article>`;
  }).join("");
  const claims = findings.map((finding) => `<div class="live-update-claim"><span>Finding</span><p>${escapeHtml(finding.statement || "")}</p></div>`).join("");
  const health = data.health || {};
  const snapshot = health.service ? `<div class="live-update-snapshot"><span>Current incident snapshot</span><strong>${escapeHtml(pretty(health.status || "unknown"))} · ${escapeHtml(formatNumber(health.api_error_rate, "%"))} API errors · ${escapeHtml(formatNumber(health.queue_depth))} queued · ${escapeHtml(formatNumber(health.database_connections))} DB connections</strong></div>` : "";
  return `<div class="live-evidence-updates"><div class="live-updates-heading"><span>INFORMATION RECEIVED</span><small>Actual observations and findings saved so far</small></div>${snapshot}${updates || '<p class="live-update-empty">Waiting for the first specialist response to be saved. The checks are still running.</p>'}${claims}</div>`;
}

function taskResults(data) {
  return Object.values(checkpoint(data).task_results || {}).map((task) => {
    const session = task?.session || {};
    return { ...task, session, role: task?.role || session.role || "unknown-agent" };
  });
}

function modelToolCalls(data) {
  const calls = [];
  for (const task of taskResults(data)) {
    for (const call of task.session?.tool_uses || []) {
      if (!call || typeof call !== "object") continue;
      const name = String(call.name || "").replace(/^.*___/, "");
      if (!name) continue;
      calls.push({ name, input: call.input || {}, role: task.role, taskId: task.task_id, source: "Harness" });
    }
  }
  return calls;
}

function allToolCount(data) {
  const events = orderedToolEvents(data);
  return events.length || modelToolCalls(data).length;
}

function agentDefinition(id) {
  return AGENTS.find((agent) => agent.id === id) || {
    id,
    label: pretty(id || "Unknown agent"),
    short: "AGT",
    purpose: "role-scoped investigation work",
    skill: "static role configuration",
    tools: [],
    accent: "violet",
  };
}

function configuredAgentSkills(agent) {
  const values = Array.isArray(agent.skills)
    ? agent.skills
    : String(agent.skill || "").split(" · ");
  return [...new Set(values.map((value) => String(value || "").trim()).filter(Boolean))];
}

function agentSkillTrace(data, task, agent) {
  const configured = configuredAgentSkills(agent);
  const catalog = data?.agent_catalog?.[agent.id] || {};
  const catalogDefinitions = Array.isArray(catalog.skill_definitions) ? catalog.skill_definitions : [];
  if (!task?.session) {
    return {
      status: "not_invoked",
      skills: [],
      definitions: catalogDefinitions,
      definitionSource: "current role definition",
      appliedVia: "No invocation recorded for this run",
      authority: "reference-only",
      attachments: [],
      note: `This role has not run in the selected incident. The current role definition is shown below: ${configured.join(" · ") || "none"}.`,
    };
  }
  const trace = task.session.skill_trace;
  if (trace && Array.isArray(trace.skills)) {
    const persistedDefinitions = Array.isArray(trace.definitions) ? trace.definitions : [];
    return {
      status: String(trace.status || "trace_recorded"),
      skills: trace.skills.map((value) => String(value || "").trim()).filter(Boolean),
      definitions: persistedDefinitions.length ? persistedDefinitions : catalogDefinitions,
      definitionSource: persistedDefinitions.length ? "persisted invocation" : "current role definition",
      appliedVia: String(trace.applied_via || "invocation configuration"),
      authority: String(trace.authority || "reference-only"),
      attachments: Array.isArray(trace.direct_harness_attachments) ? trace.direct_harness_attachments : [],
      note: persistedDefinitions.length
        ? "Persisted from this invocation; skill guidance cannot authorize a state change."
        : "The skill name was persisted, but this task predates skill-body tracing. The current role definition is shown below; the historical prompt body is unavailable.",
    };
  }
  return {
    status: "trace_unavailable",
    skills: configured,
    definitions: catalogDefinitions,
    definitionSource: "current role definition",
    appliedVia: "Legacy invocation without a persisted skill trace",
    authority: "reference-only",
    attachments: [],
    note: "This older task has no execution trace. The current role definition is shown below; it is not a replay of the historical invocation.",
  };
}

function renderAgentWorkspace(data, agent) {
  const tasks = taskResults(data).filter((task) => task.role === agent.id);
  const calls = modelToolCalls(data).filter((call) => call.role === agent.id);
  const observedTools = [...new Map(calls.map((call) => [call.name, call])).values()];
  const status = agentState(agent, data);
  const stateLabel = status === "complete" ? "Complete" : status === "active" ? "Working now" : status === "failed" || status === "attention" ? "Needs attention" : "Waiting";
  const latestTask = tasks[tasks.length - 1];
  const session = latestTask?.session || {};
  const iterations = session.metrics?.iteration_count || session.metrics?.turn_count || "—";
  const toolMarkup = observedTools.length
    ? observedTools.map((call) => `<span class="agent-tool-chip called">${escapeHtml(displayToolName(call.name))}</span>`).join("")
    : '<span class="agent-tool-empty">No Harness tool selections persisted yet.</span>';
  const allowedMarkup = agent.tools.length
    ? agent.tools.map((tool) => `<span class="agent-tool-chip">${escapeHtml(displayToolName(tool))}</span>`).join("")
    : '<span class="agent-tool-empty">No additional tools configured.</span>';
  return `<article class="agent-workspace-card ${status} ${agent.accent}"><div class="agent-workspace-heading"><div class="agent-workspace-identity"><div class="agent-avatar">${escapeHtml(agent.short)}</div><div><p class="eyebrow">ROLE-SCOPED HARNESS SESSION</p><h3>${escapeHtml(agent.label)}</h3><p>${escapeHtml(agent.purpose)}</p></div></div>${statusPill(stateLabel, status === "complete" ? "good" : status === "failed" ? "bad" : status === "attention" ? "warn" : status === "active" ? "good" : "neutral", false)}</div><div class="agent-workspace-facts"><div><span>Role skill definition</span><strong>${escapeHtml(agent.skill)}</strong><small>reference guidance; open agent details for the invocation trace</small></div><div><span>Run activity</span><strong>${escapeHtml(tasks.length)} task${tasks.length === 1 ? "" : "s"} · ${escapeHtml(calls.length)} tool call${calls.length === 1 ? "" : "s"}</strong><small>${escapeHtml(iterations)} Harness iteration${iterations === 1 ? "" : "s"} · ${escapeHtml(latestTask?.status ? pretty(latestTask.status) : "not started")}</small></div></div><div class="agent-workspace-tools"><div><span>Tools called in this run</span><div class="agent-tool-list">${toolMarkup}</div></div><div><span>Role allowlist</span><div class="agent-tool-list">${allowedMarkup}</div></div></div></article>`;
}

function renderAgentWorkspaces(data, compact = false) {
  return `<section class="agent-workspaces ${compact ? "compact" : ""}"><div class="section-heading"><div><p class="eyebrow">AGENT WORKSPACES</p><h2>Separate roles around one incident</h2><p class="muted">Each panel is a separate role-scoped Harness session. Three evidence specialists run in parallel; the orchestrator reconciles the saved analysis.</p></div><span class="topology-count">${AGENTS.length} configured roles</span></div><div class="agent-workspace-grid">${AGENTS.map((agent) => renderAgentWorkspace(data, agent)).join("")}</div></section>`;
}

function investigationStall(data) {
  const cp = checkpoint(data);
  if (state.investigateInFlight || cp.stage !== "parallel_investigation" || taskResults(data).length > 0) return null;
  const startedAt = new Date(data?.run?.created_at || 0).getTime();
  if (!Number(cp.retry_count || 0) && Number.isFinite(startedAt) && Date.now() - startedAt < 30_000) return null;
  const ev = evidence(data);
  const hasActivity = allToolCount(data) > 0 || (ev.findings || []).length > 0 || (ev.evidence || []).length > 0;
  return hasActivity
    ? {
        kind: "handoff",
        label: "Needs attention",
        message: "Evidence checks ran, but the investigation did not save its handoff to the next business step.",
        action: "Resume investigation",
      }
    : {
        kind: "no_result",
        label: "No evidence yet",
        message: "The first evidence checks have not returned. The investigation is not moving forward.",
        action: "Retry evidence collection",
      };
}

function isEvidenceStalled(data) {
  return Boolean(investigationStall(data));
}

function activeAgent(data) {
  const stage = checkpoint(data).stage;
  if (stage === "reconcile_evidence") return "orchestrator";
  if (stage === "parallel_investigation") return "telemetry-agent · change-agent · data-agent";
  const tasks = taskResults(data);
  return tasks.length ? tasks[tasks.length - 1].role : "orchestrator";
}

function businessOwner(data) {
  const stage = checkpoint(data).stage;
  if (["initialize", "parallel_investigation", "reconcile_evidence", "awaiting_follow_up"].includes(stage)) return "Investigation team";
  if (isAnalysisCompleteStage(stage)) return "Investigation lead";
  return "Investigation team";
}

function businessImpact(health) {
  const status = String(health?.status || "").toLowerCase();
  const apiErrors = Number(health?.api_error_rate || 0);
  const queueDepth = Number(health?.queue_depth || 0);
  const orderDelay = Number(health?.order_processing_delay_seconds || 0);
  if (status === "healthy" && apiErrors <= 1 && queueDepth <= 1000 && orderDelay <= 10) return "No impact detected";
  if (apiErrors > 1 || queueDepth > 1000 || orderDelay > 10 || status === "degraded") return "Customer impact detected";
  return "Impact being assessed";
}

function businessStatus(data) {
  const stage = checkpoint(data).stage || "initialize";
  const stall = investigationStall(data);
  if (stall) return { label: stall.label, tone: "warn", message: stall.message };
  const live = isLive(data);
  if (stage === "awaiting_follow_up") return { label: "Needs more evidence", tone: "warn", message: "The investigation has an open question that must be answered before analysis can complete." };
  if (isAnalysisCompleteStage(stage)) return { label: "Analysis complete", tone: "good", message: "The read-only analysis is complete. Review the evidence-grounded findings and hypotheses." };
  if (live) return { label: "Investigation in progress", tone: "good", message: "The team is gathering evidence to explain the incident and its customer impact." };
  return { label: "Ready for next step", tone: "neutral", message: "The run is loaded from the latest saved investigation state." };
}

function businessToolLabel(value) {
  const name = String(value || "").toLowerCase();
  if (name.includes("get_shared_evidence")) return "Shared evidence reconciled";
  if (name.includes("calculate_customer_impact")) return "Customer impact calculated";
  if (name.includes("get_queue_metrics")) return "Queue impact checked";
  if (name.includes("get_database_metrics")) return "Database impact checked";
  if (name.includes("service_health")) return "Service health checked";
  if (name.includes("deployment") || name.includes("release") || name.includes("config")) return "Recent changes reviewed";
  if (name.includes("metric") || name.includes("log") || name.includes("trace") || name.includes("alarm")) return "Performance signals reviewed";
  if (name.includes("database")) return "Database impact checked";
  if (name.includes("queue")) return "Queue impact checked";
  if (name.includes("order")) return "Order processing checked";
  if (name.includes("compare")) return "Signals compared";
  return value ? "Evidence collected" : "Starting evidence checks";
}

function activityAgentForEvent(event, data) {
  const explicit = String(event?.role || event?.agent || "").trim();
  if (explicit) return explicit;
  const toolName = String(event?.tool_name || "").replace(/^.*___/, "");
  const calls = modelToolCalls(data).filter((call) => call.name === toolName);
  const callRoles = [...new Set(calls.map((call) => call.role).filter(Boolean))];
  if (callRoles.length === 1) return callRoles[0];
  const stage = checkpoint(data).stage;
  if (stage === "parallel_investigation") {
    const specialist = AGENTS.find((agent) => agent.id !== "orchestrator" && agent.tools.includes(toolName));
    if (specialist) return specialist.id;
  }
  return "";
}

function activityResultDetail(event, data) {
  const status = String(event?.status || "ok");
  if (status !== "ok") return `${pretty(status)} · ${displayToolName(event?.tool_name)} returned an error`;
  const facts = responseFacts(event, data).slice(0, 2);
  if (facts.length) return facts.join(" · ");
  const count = (event?.evidence_ids || []).length;
  return count ? `${count} supporting record${count === 1 ? "" : "s"} saved` : "Structured result saved; no source record attached";
}

function hasLiveEvidenceActivity(data) {
  return isLive(data) && (state.investigateInFlight || allToolCount(data) > 0);
}

function isLive(data) {
  const status = String(data?.run?.status || "").toLowerCase();
  const stage = checkpoint(data).stage;
  if (state.investigateInFlight || state.followUpInFlight) return true;
  const waitingForAction = stage === "awaiting_follow_up" || isAnalysisCompleteStage(stage);
  if (waitingForAction || isEvidenceStalled(data)) return false;
  return status.includes("investig") || ["initialize", "parallel_investigation", "reconcile_evidence"].includes(stage);
}

function updateConnection(data) {
  const indicator = $("connection-status");
  if (!indicator) return;
  if (state.noticeTone === "bad") {
    indicator.className = "status-pill bad";
    indicator.innerHTML = '<span class="status-dot"></span> OFFLINE';
    return;
  }
  if (!data) {
    indicator.className = "status-pill neutral";
    indicator.innerHTML = '<span class="status-dot"></span> STANDBY';
    return;
  }
  const live = isLive(data);
  const stall = investigationStall(data);
  const stalled = Boolean(stall);
  const tone = live ? "good" : stalled ? "warn" : toneForStatus(data.run?.status || checkpoint(data).stage);
  indicator.className = `status-pill ${tone}`;
  indicator.innerHTML = `<span class="status-dot"></span> ${live ? "LIVE TRACE" : stalled ? (stall.kind === "handoff" ? "NEEDS ATTENTION" : "NO EVIDENCE") : escapeHtml(String(data.run?.status || "CONNECTED").toUpperCase())}`;
}

function setNotice(message, tone = "") {
  state.notice = message || "";
  state.noticeTone = tone;
  const notice = $("notice");
  if (!notice) return;
  // Once a run exists, actionable failures are rendered in the business trace
  // so the operator sees them beside the affected agent work instead of as a
  // detached red banner under the launch controls.
  const inTrace = Boolean(state.data && tone === "bad");
  notice.className = `notice ${tone}${inTrace ? " hidden" : ""}`;
  notice.textContent = message || "";
}

function renderLogin() {
  const shell = $("auth-shell");
  const consoleShell = $("console-shell");
  if (!shell || !consoleShell) return;
  consoleShell.classList.add("hidden");
  shell.classList.remove("hidden");
  if (state.authLoading) {
    shell.innerHTML = `<section class="auth-panel"><p class="eyebrow">OPSFUSION ACCESS</p><h2>Checking operator session…</h2><p class="muted">Validating the Cognito ID token before opening the investigation console.</p></section>`;
    return;
  }
  const challenge = state.authChallenge?.name === "NEW_PASSWORD_REQUIRED";
  shell.innerHTML = `<section class="login-hero">
    <div class="auth-mark" aria-hidden="true">OF</div>
    <p class="auth-kicker">OPSFUSION / SYNTHETIC OPERATIONS</p>
    <h1>Investigate the incident. Keep authority explicit.</h1>
    <p class="lead">The console is an invite-only operator surface for the OpsFusion simulator. Cognito authenticates the person at the keyboard; the API independently enforces which operations that identity may perform.</p>
    <div class="auth-chips"><span>Cognito ID tokens</span><span>Admin-created users</span><span>Admin-only invitations</span><span>Read-only analysis</span></div>
  </section>
  <section class="auth-panel">
    <p class="eyebrow">OPERATOR SIGN-IN</p>
    <h2>${challenge ? "Set your first password" : "Sign in to the control room"}</h2>
    <p class="muted">${challenge ? "An administrator invited this account. Choose a permanent password to finish activation." : "An administrator must invite your email address before you can sign in."}</p>
    ${challenge ? '<div class="auth-challenge"><strong>First-time sign-in</strong><br />Cognito requires a permanent password before issuing the session used by the console.</div>' : ""}
    <form id="auth-form" class="stack-form">
      <label>Email address<input id="auth-username" type="email" autocomplete="username" required value="${escapeHtml(state.authChallenge?.username || "")}" /></label>
      ${challenge ? '<label>New password<input id="auth-new-password" type="password" autocomplete="new-password" minlength="12" required /></label>' : '<label>Password<input id="auth-password" type="password" autocomplete="current-password" required /></label>'}
      <button id="auth-submit" type="submit">${challenge ? "Activate account" : "Sign in"}</button>
      ${challenge ? '<button id="auth-back" type="button" class="secondary">Back to sign in</button>' : ""}
    </form>
    ${state.authError ? `<p class="auth-error" role="alert">${escapeHtml(state.authError)}</p>` : ""}
    <p class="auth-footnote">This deployment keeps the Function URL as transport only. Protected API routes require a verified Cognito ID token, and admin operations additionally require the <code>opsfusion-admin</code> group.</p>
  </section>`;
  $("auth-form")?.addEventListener("submit", submitLogin);
  $("auth-back")?.addEventListener("click", () => {
    state.authChallenge = null;
    state.authError = "";
    renderAuthShell();
  });
}

function renderAuthenticated() {
  const shell = $("auth-shell");
  const consoleShell = $("console-shell");
  if (!shell || !consoleShell) return;
  shell.classList.add("hidden");
  consoleShell.classList.remove("hidden");
  const identity = state.principal?.email || state.principal?.username || "operator";
  const chip = $("principal-chip");
  if (chip) {
    chip.textContent = `${identity}${state.principal?.is_admin ? " · admin" : ""}`;
    chip.title = identity;
    chip.classList.remove("hidden");
  }
  $("sign-out")?.classList.remove("hidden");
  render();
}

function renderAuthShell() {
  if (state.principal) renderAuthenticated();
  else renderLogin();
}

async function submitLogin(event) {
  event.preventDefault();
  if (state.busy === "auth") return;
  const username = $("auth-username")?.value.trim() || "";
  state.authError = "";
  state.busy = "auth";
  try {
    const result = state.authChallenge
      ? await api("/auth/respond-to-challenge", { method: "POST", body: JSON.stringify({ username, new_password: $("auth-new-password")?.value || "", session: state.authChallenge.session }) }, false)
      : await api("/auth/login", { method: "POST", body: JSON.stringify({ username, password: $("auth-password")?.value || "" }) }, false);
    if (result.status === "challenge_required") {
      state.authChallenge = { ...result.challenge, username };
      state.authError = "";
      state.busy = null;
      renderLogin();
      return;
    }
    if (result.status !== "authenticated" || !result.id_token) throw new Error("Cognito did not return a usable ID token");
    state.session = result;
    saveSession(result);
    state.authChallenge = null;
    state.principal = await api("/auth/me");
    state.authLoading = false;
    state.busy = null;
    renderAuthenticated();
    if (state.runId) {
      await refresh();
    }
  } catch (error) {
    state.busy = null;
    state.authError = error.message || "Sign-in failed";
    if (!state.session) renderLogin();
  }
}

function signOut(message = "") {
  clearSession();
  state.principal = null;
  state.authChallenge = null;
  state.authLoading = false;
  state.authError = message;
  state.data = null;
  state.runId = null;
  state.lastRenderSignature = "";
  state.selectedAgentId = null;
  state.busy = null;
  state.followUpInFlight = false;
  state.followUpError = "";
  state.followUpStartedAt = null;
  stopProgressTimer();
  state.notice = "";
  if (state.pollTimer) window.clearInterval(state.pollTimer);
  state.pollTimer = null;
  renderAuthShell();
}

async function bootstrapAuth() {
  renderLogin();
  if (state.session?.id_token) {
    try {
      state.principal = await api("/auth/me");
      state.authLoading = false;
      renderAuthenticated();
      if (state.runId) {
        await refresh();
      }
      return;
    } catch (error) {
      if (error.status === 401) clearSession();
      else {
        state.authError = error.message || "Unable to validate the saved session";
        state.authLoading = false;
        renderLogin();
        return;
      }
    }
  }
  try {
    const principal = await api("/auth/me", {}, false);
    if (principal && principal.auth_required === false) {
      state.principal = principal;
      state.authLoading = false;
      renderAuthenticated();
      if (state.runId) {
        await refresh();
      }
      return;
    }
  } catch (error) {
    if (error.status && error.status !== 401) state.authError = error.message || "Authentication service unavailable";
  }
  state.authLoading = false;
  renderLogin();
}

function renderRunBanner(data) {
  const run = data.run || {};
  const cp = checkpoint(data);
  const health = data.health || {};
  const stage = cp.stage || run.status || "not started";
  const status = businessStatus(data);
  const completed = cp.completed_stages || [];
  const totalSteps = STAGES.length - 1;
  const progressSteps = isAnalysisCompleteStage(stage) ? totalSteps : Math.min(completed.length, totalSteps);
  const savedLabel = isEvidenceStalled(data) ? `v${cp.version ?? 0} · no evidence` : `v${cp.version ?? 0}`;
  const bannerMessage = status.message;
  $("run-status-summary").innerHTML = `${statusPill(status.label, status.tone)}<span class="summary-id">${escapeHtml(shortId(run.run_id || state.runId))}</span>`;
  $("run-banner").innerHTML = `<section class="run-banner panel">
    <div class="run-banner-main">
      <div class="run-signal ${status.tone === "good" ? "live" : ""}"><span></span></div>
      <div><p class="eyebrow">INCIDENT ${escapeHtml(run.scenario_id || "scenario")}</p><h2>${escapeHtml(stageLabel(stage))}</h2><p class="muted">${escapeHtml(bannerMessage)}</p></div>
    </div>
    <div class="run-facts">
      <div><span>Owner</span><strong>${escapeHtml(businessOwner(data))}</strong></div>
      <div><span>Progress</span><strong>${escapeHtml(progressSteps)} of ${escapeHtml(totalSteps)} steps</strong></div>
      <div><span>Customer impact</span><strong>${escapeHtml(businessImpact(health))}</strong></div>
      <div><span>Last saved</span><strong>${escapeHtml(savedLabel)}</strong></div>
    </div>
  </section>`;
}

function renderStageTrack(data) {
  return `<div class="stage-track" role="list" aria-label="Investigation progress">
    ${STAGES.map((stage) => {
      const status = stageState(stage.id, data);
      const marker = status === "complete" ? "✓" : status === "attention" ? "!" : stage.number;
      return `<div class="stage-step ${status}" role="listitem" title="${escapeHtml(stage.detail)}">
        <div class="stage-marker">${marker}</div>
        <div class="stage-copy"><strong>${escapeHtml(stage.label)}</strong><small>${escapeHtml(stage.detail)}</small></div>
      </div>`;
    }).join("")}
  </div>`;
}

function renderWorkflowDetails(data) {
  const cp = checkpoint(data);
  const currentStageId = cp.stage || "initialize";
  const currentStage = STAGES.find((stage) => stage.id === currentStageId) || {
    id: currentStageId,
    label: stageLabel(currentStageId),
    detail: "checkpoint state",
  };
  const tasks = taskResults(data);
  const completedStages = cp.completed_stages || [];
  const health = data.health || {};
  const status = businessStatus(data);
  const totalSteps = STAGES.length - 1;
  const progressSteps = isAnalysisCompleteStage(currentStageId) ? totalSteps : Math.min(completedStages.length, totalSteps);
  const stall = investigationStall(data);
  const evidenceStalled = Boolean(stall);
  const taskMarkup = tasks.length
    ? tasks.map((task) => `<div class="workflow-task-row"><span class="workflow-task-dot ${toneForStatus(task.status)}"></span><div><strong>${escapeHtml(agentLabel(task.role || ""))}</strong><small>${escapeHtml(pretty(task.status || "pending"))}</small></div></div>`).join("")
    : evidenceStalled
      ? `<div class="workflow-empty-detail stalled-detail"><strong>${escapeHtml(stall.kind === "handoff" ? "Evidence checks ran, but the handoff was not saved." : "No specialist result has been saved.")}</strong><span>${escapeHtml(stall.kind === "handoff" ? "The next business step is to confirm the likely cause. Resume the investigation to continue from the evidence already collected." : `The run started ${elapsedSince(data.run?.created_at)} ago, but evidence collection has not returned.`)}</span></div>`
      : '<p class="workflow-empty-detail">No work has been recorded yet. The investigation may still be starting.</p>';
  const requestMarkup = currentStageId === "awaiting_follow_up"
    ? `<div class="workflow-detail-subsection"><span class="workflow-detail-label">Questions these checks will answer</span>${followUpReasons(data).map((reason) => `<div class="workflow-request-row"><span>?</span><p>${escapeHtml(reason.text)}</p></div>`).join("")}<p class="workflow-detail-note">These read-only checks run automatically and determine whether the evidence is sufficient to complete the analysis. They do not change the incident.</p></div>`
    : "";
  return `<div id="workflow-details" class="workflow-details" data-workflow-details>
    <div class="workflow-details-intro">
      <div><p class="eyebrow">INVESTIGATION DETAILS</p><h3>What the team is doing now</h3><p class="muted">This view explains the current business step, what has been completed, and what needs to happen next.</p></div>
      ${statusPill(status.label, status.tone, false)}
    </div>
    <div class="workflow-detail-facts">
      <div><span>Current step</span><strong>${escapeHtml(currentStage.label)}</strong><small>${escapeHtml(currentStage.detail)}</small></div>
      <div><span>Progress</span><strong>${escapeHtml(progressSteps)} of ${escapeHtml(totalSteps)} complete</strong><small>last saved update v${escapeHtml(cp.version ?? 0)}</small></div>
      <div><span>Case status</span><strong>${escapeHtml(status.label)}</strong><small>${escapeHtml(businessImpact(health))}</small></div>
      <div><span>Owner</span><strong>${escapeHtml(businessOwner(data))}</strong><small>responsible for the next step</small></div>
    </div>
    <div class="workflow-details-columns">
      <div class="workflow-detail-column"><div class="workflow-detail-title">Investigation steps</div><div class="workflow-stage-list">${STAGES.map((stage) => {
        const status = stageState(stage.id, data);
        return `<div class="workflow-stage-row ${status}"><span class="workflow-stage-marker">${status === "complete" ? "✓" : status === "attention" ? "!" : stage.number}</span><div><strong>${escapeHtml(stage.label)}</strong><small>${escapeHtml(stage.detail)}</small></div><span class="workflow-stage-status">${escapeHtml(stageStatusLabel(status))}</span></div>`;
      }).join("")}</div></div>
      <div class="workflow-detail-column"><div class="workflow-detail-title">What happens next</div><div class="workflow-transition-card ${evidenceStalled ? "stalled" : ""}"><strong>${escapeHtml(evidenceStalled ? stall.action : currentStage.label)}</strong><p>${escapeHtml(evidenceStalled ? (stall.kind === "handoff" ? "The evidence is available, but the workflow has not moved to confirming the likely cause. Resume the investigation to complete that handoff." : "No specialist checks have returned, so the investigation cannot complete the analysis yet.") : workflowNextStep(currentStageId))}</p></div><div class="workflow-detail-subsection"><span class="workflow-detail-label">Work completed in this step</span><div class="workflow-task-list">${taskMarkup}</div></div>${requestMarkup}</div>
    </div>
    ${renderAgentWorkspaces(data)}
  </div>`;
}

function renderHealthMetrics(health) {
  const metrics = [
    ["API error rate", formatNumber(health.api_error_rate, "%"), "normal baseline 0.3%", Number(health.api_error_rate) > 1 ? "bad" : "good"],
    ["API p95 latency", formatNumber(health.api_p95_latency_ms, " ms"), "request experience", Number(health.api_p95_latency_ms) > 2000 ? "bad" : "good"],
    ["DB connections", formatNumber(health.database_connections), "pool pressure", Number(health.database_connections) > 100 ? "bad" : "good"],
    ["SQS queue depth", formatNumber(health.queue_depth), "messages waiting", Number(health.queue_depth) > 1000 ? "bad" : "good"],
    ["Order delay", formatNumber(health.order_processing_delay_seconds, " s"), "customer-visible lag", Number(health.order_processing_delay_seconds) > 10 ? "bad" : "good"],
  ];
  return `<div class="metric-grid">${metrics.map(([label, value, detail, tone]) => `<div class="metric-card ${tone}"><span>${escapeHtml(label)}</span><strong>${value}</strong><small>${escapeHtml(detail)}</small></div>`).join("")}</div>`;
}

function renderOuterWorkflow(data) {
  const cp = checkpoint(data);
  const completed = cp.completed_stages || [];
  const totalSteps = STAGES.length - 1;
  const progressSteps = isAnalysisCompleteStage(cp.stage) ? totalSteps : Math.min(completed.length, totalSteps);
  const stall = investigationStall(data);
  const evidenceStalled = Boolean(stall);
  const followUpRunning = state.followUpInFlight && state.busy === "follow-up";
  const followUpObserved = Math.max(0, allToolCount(data) - Number(state.followUpInitialToolCount || 0));
  const followUpProgress = followUpRunning ? `
    <div class="workflow-progress" role="status" aria-live="polite">
      <span class="progress-spinner" aria-hidden="true"></span>
      <div><strong>Read-only evidence checks are running</strong><small>${escapeHtml(followUpObserved ? `${followUpObserved} new evidence checks have returned.` : "Waiting for the first evidence check to return.")}</small><span><span data-follow-up-runtime>Running for ${escapeHtml(elapsedSince(state.followUpStartedAt))}</span>. No service change is being made.</span></div>
    </div>` : "";
  const followUpReasonMarkup = cp.stage === "awaiting_follow_up"
    ? `<div class="workflow-action-reasons"><span>Analysis questions</span><ul>${followUpReasons(data).map((reason) => `<li>${escapeHtml(reason.text)}</li>`).join("")}</ul></div>`
    : "";
  const followUpFailures = Array.isArray(cp.metadata?.follow_up_failures) ? cp.metadata.follow_up_failures : [];
  const followUpFailureMarkup = (state.followUpError || followUpFailures.length) && cp.stage === "awaiting_follow_up"
    ? `<div class="workflow-error" role="alert"><strong>Automatic evidence checks are still incomplete.</strong><p>${escapeHtml(state.followUpError || "The checks returned, but the saved workflow still needs the items listed below. Automatic retries will continue.")}</p>${followUpFailures.map((failure) => `<div><span>${escapeHtml(agentLabel(failure.role || "specialist"))}</span><small>${escapeHtml(failure.error || "This check did not return a usable result.")}</small></div>`).join("")}<small class="workflow-error-footnote">Saved checkpoint v${escapeHtml(cp.version ?? 0)}. No service change was made.</small></div>`
    : "";
  const followUpFailed = Boolean(state.followUpError || followUpFailures.length);
  const followUpAction = cp.stage === "awaiting_follow_up" ? `
    <div class="workflow-action" role="status" aria-label="Automatic evidence checks">
      <div class="workflow-action-copy"><span class="workflow-action-kicker">${followUpFailed ? "AUTOMATIC CHECKS NEED ATTENTION" : "AUTOMATED EVIDENCE CHECKS"}</span><strong>${followUpFailed ? "The read-only evidence checks have not completed." : "Closing the evidence gap before analysis is complete."}</strong><small>${followUpFailed ? "The workflow is retrying these checks automatically; no operator action is required." : "These checks run automatically because they cannot change the incident."}</small>${followUpProgress}${followUpFailureMarkup}${followUpReasonMarkup}${renderLiveEvidenceUpdates(data)}</div>
    </div>` : "";
  const stalledAction = evidenceStalled ? `
    <div class="workflow-action workflow-stalled-action" role="alert" aria-label="Evidence collection needs attention">
      <div class="workflow-action-copy"><span class="workflow-action-kicker">NEEDS ATTENTION</span><strong>${escapeHtml(stall.kind === "handoff" ? "Evidence was collected, but the workflow did not advance." : "No evidence has been saved yet.")}</strong><small>${escapeHtml(stall.kind === "handoff" ? "The team checked the incident, but the result was not handed off to the next business step. Nothing has changed in the simulated incident." : `The investigation started ${elapsedSince(data.run?.created_at)} ago, but none of the specialist checks returned. Nothing has changed in the simulated incident.`)}</small><div class="workflow-action-reasons"><span>Next step</span><p>${escapeHtml(stall.kind === "handoff" ? "Resume the investigation to confirm the likely cause from the evidence already collected." : "Retry evidence collection so the team can identify the cause and assess the customer impact.")}</p></div></div>
      <button id="retry-investigation" type="button" ${state.busy === "investigate" ? "disabled" : ""}>${state.busy === "investigate" ? "Resuming…" : escapeHtml(stall.action)}</button>
    </div>` : "";
  return `<section class="panel workflow-panel" data-workflow-panel>
    <div class="section-heading workflow-heading">
      <div><p class="eyebrow">INVESTIGATION PROGRESS</p><h2>What happens next</h2><p class="muted">Follow the incident from evidence gathering to evidence-grounded analysis. Click anywhere in this panel to see the details.</p></div>
      <div class="workflow-heading-actions"><div class="workflow-label"><span class="workflow-icon">OF</span><span><strong>Investigation flow</strong><small>${progressSteps} of ${totalSteps} steps complete</small></span></div><button id="workflow-toggle" class="workflow-toggle" type="button" aria-expanded="${state.workflowExpanded ? "true" : "false"}" aria-controls="workflow-details">${state.workflowExpanded ? "Hide details" : "View details"}</button></div>
    </div>
    ${renderStageTrack(data)}
    <div class="workflow-bottom-line"><span><i class="legend-line completed-line"></i>completed</span><span><i class="legend-line active-line"></i>current</span><span><i class="legend-line pending-line"></i>up next</span><span class="workflow-current">Current: <strong>${escapeHtml(stageLabel(cp.stage || "initialize"))}</strong></span></div>
    ${stalledAction}
    ${followUpAction}
    ${state.workflowExpanded ? renderWorkflowDetails(data) : ""}
  </section>`;
}

function loopNode(kind, number, title, detail, active, extra = "") {
  return `<div class="loop-node ${kind} ${active ? "active" : ""}"><span class="loop-number">${number}</span><div><strong>${escapeHtml(title)}</strong><small>${escapeHtml(detail)}</small>${extra}</div></div>`;
}

function renderHarnessLoop(data) {
  const events = orderedToolEvents(data);
  const calls = modelToolCalls(data);
  const latest = events[events.length - 1] || calls[calls.length - 1];
  const iterationCount = taskResults(data).reduce((total, task) => total + Number(task.session?.metrics?.iteration_count || task.session?.metrics?.turn_count || 0), 0) || taskResults(data).length;
  const latestTool = latest?.tool_name || latest?.name;
  const currentStage = stageLabel(checkpoint(data).stage || "initialize");
  return `<section class="panel live-evidence-panel">
    <div class="section-heading">
      <div><p class="eyebrow">LIVE EVIDENCE COLLECTION</p><h2>What the team is checking</h2><p class="muted">The investigation is actively gathering evidence for the current business step.</p></div>
      <div class="iteration-badge pulsing"><strong>${escapeHtml(iterationCount || 0)}</strong><span>checks<br />observed</span></div>
    </div>
    <div class="live-evidence-grid">
      <div class="live-evidence-card"><span>Current focus</span><strong>${escapeHtml(currentStage)}</strong><small>the team is working on this step now</small></div>
      <div class="live-evidence-card"><span>Latest check</span><strong>${escapeHtml(businessToolLabel(latestTool))}</strong><small>${latestTool ? "evidence has been returned to the investigation" : "waiting for the first evidence result"}</small></div>
      <div class="live-evidence-card"><span>Business question</span><strong>What changed, and who is affected?</strong><small>findings will support the final analysis</small></div>
    </div>
    <div class="live-evidence-note"><span class="pulse-dot live"></span><strong>Investigation in progress.</strong><span>New evidence will appear here as checks complete.</span></div>
  </section>`;
}

function agentState(agent, data) {
  const stage = checkpoint(data).stage;
  const tasks = taskResults(data).filter((task) => task.role === agent.id);
  if (tasks.some((task) => task.status === "completed")) return "complete";
  if (tasks.some((task) => task.status === "failed")) return "failed";
  if (agent.id === "orchestrator" && ["reconcile_evidence", "completed"].includes(stage)) return stage === "reconcile_evidence" ? "active" : "complete";
  if (agent.id === "telemetry-agent" || agent.id === "change-agent" || agent.id === "data-agent") return stage === "parallel_investigation" ? (isEvidenceStalled(data) ? "attention" : "active") : "queued";
  return "queued";
}

function renderAgentRoster(data, compact = false) {
  const selectedAgent = agentDefinition(state.selectedAgentId || "");
  return `<div class="agent-roster-wrap"><div class="agent-roster ${compact ? "compact" : ""}">${AGENTS.map((agent) => {
    const status = agentState(agent, data);
    const taskCount = taskResults(data).filter((task) => task.role === agent.id).length;
    const toolCount = modelToolCalls(data).filter((call) => call.role === agent.id).length;
    const stateLabel = status === "complete" ? "complete" : status === "active" ? "working now" : status === "failed" || status === "attention" ? "needs attention" : "waiting";
    const selected = state.selectedAgentId === agent.id;
    return `<button class="agent-card ${status} ${agent.accent} ${selected ? "selected" : ""}" type="button" data-agent-id="${escapeHtml(agent.id)}" aria-pressed="${selected ? "true" : "false"}" aria-label="View details for ${escapeHtml(agent.label)}"><div class="agent-avatar">${escapeHtml(agent.short)}</div><div class="agent-copy"><strong>${escapeHtml(agent.label)}</strong><small>${escapeHtml(agent.purpose)}</small>${!compact ? `<span>${stateLabel} · ${taskCount} task${taskCount === 1 ? "" : "s"} · ${toolCount} model calls</span>` : ""}</div><span class="agent-card-status"><span class="agent-state">${escapeHtml(stateLabel)}</span><span class="agent-card-action">Details →</span></span></button>`;
  }).join("")}</div>${state.selectedAgentId && selectedAgent.id ? renderAgentInspector(data, selectedAgent) : '<p class="agent-roster-hint">Select an agent to inspect its skill, Harness session, tools, and authority boundary.</p>'}</div>`;
}

function agentObservedTools(data, agentId) {
  const names = modelToolCalls(data)
    .filter((call) => call.role === agentId)
    .map((call) => call.name);
  orderedToolEvents(data)
    .filter((event) => event.role === agentId)
    .forEach((event) => names.push(String(event.tool_name || "").replace(/^.*___/, "")));
  return [...new Set(names.filter(Boolean))];
}

function renderAgentInspector(data, agent) {
  const tasks = taskResults(data).filter((task) => task.role === agent.id);
  const task = tasks[tasks.length - 1];
  const session = task?.session || {};
  const skillTrace = agentSkillTrace(data, task, agent);
  const promptTrace = session.prompt_trace && typeof session.prompt_trace === "object" ? session.prompt_trace : null;
  const calls = modelToolCalls(data).filter((call) => call.role === agent.id);
  const observedTools = agentObservedTools(data, agent.id);
  const status = agentState(agent, data);
  const stateLabel = status === "complete" ? "Complete" : status === "active" ? "Working now" : status === "failed" || status === "attention" ? "Needs attention" : "Waiting";
  const iterations = session.metrics?.iteration_count || session.metrics?.turn_count || "—";
  const authority = "Read-only analysis access; this role cannot modify simulator state.";
  const chips = (items, empty) => items.length
    ? items.map((item) => `<span class="agent-tool-chip">${escapeHtml(displayToolName(item))}</span>`).join("")
    : `<span class="agent-tool-empty">${escapeHtml(empty)}</span>`;
  const skillStatus = skillTrace.status === "applied"
    ? "Applied"
    : skillTrace.status === "not_applied"
      ? "Not applied by local stub"
      : skillTrace.status === "not_invoked"
        ? "Not invoked yet"
        : skillTrace.status === "trace_unavailable"
          ? "Trace unavailable"
          : "Recorded";
  const skillChips = skillTrace.skills.length
    ? skillTrace.skills.map((skill) => `<span class="agent-tool-chip called">${escapeHtml(skill)}</span>`).join("")
    : '<span class="agent-tool-empty">No skill execution record yet.</span>';
  const attachmentChips = skillTrace.attachments.length
    ? skillTrace.attachments.map((item) => {
        const label = typeof item === "string" ? item : item?.name || item?.skill || "Harness skill attachment";
        return `<span class="agent-tool-chip">${escapeHtml(label)}</span>`;
      }).join("")
    : '<span class="agent-tool-empty">None — this role uses reference guidance in its system prompt.</span>';
  const skillDefinitions = skillTrace.definitions.length
    ? skillTrace.definitions.map((definition) => `<article class="agent-skill-definition"><strong>${escapeHtml(definition.name || "Skill")}</strong><p>${escapeHtml(definition.instructions || "No instructions recorded.")}</p></article>`).join("")
    : '<p class="agent-tool-empty">Skill instructions were not persisted for this invocation. Start a new invocation to capture them.</p>';
  const currentSystemPrompt = String(data?.agent_catalog?.[agent.id]?.system_prompt || "");
  const recordedSystemPrompt = String(promptTrace?.system_prompt || "");
  const systemPrompt = recordedSystemPrompt || currentSystemPrompt;
  const userPrompt = String(promptTrace?.user_prompt || "");
  const promptRecorded = Boolean(recordedSystemPrompt || userPrompt);
  const promptMarkup = systemPrompt || userPrompt
    ? `<div class="agent-inspector-prompts"><div class="agent-inspector-section-heading"><div><span>Prompt trace</span><strong>${promptRecorded ? "What was sent to this agent" : "Current prompt template"}</strong></div><small>${escapeHtml(promptRecorded ? (promptTrace?.system_prompt_source || "Invocation record") : "Historical prompt not persisted")}</small></div>${systemPrompt ? `<details class="agent-prompt-block"><summary>${promptRecorded ? "Actual Harness system prompt" : "Current Harness system prompt template"}</summary><pre>${escapeHtml(systemPrompt)}</pre></details>` : '<p class="agent-tool-empty">No system prompt was sent by the selected runtime.</p>'}${userPrompt ? `<details class="agent-prompt-block"><summary>Agent invocation prompt</summary><pre>${escapeHtml(userPrompt)}</pre></details>` : ""}${!promptRecorded ? '<p class="agent-inspector-note">This task predates prompt tracing, so the exact historical invocation prompt is unavailable. New invocations persist both prompts.</p>' : ""}</div>`
    : '<div class="agent-inspector-prompts"><div class="agent-inspector-section-heading"><div><span>Prompt trace</span><strong>Not available for this task</strong></div></div><p class="agent-tool-empty">This persisted task predates prompt tracing. New invocations record the exact system and invocation prompts here.</p></div>';
  return `<section id="agent-inspector" class="agent-inspector ${agent.accent}" aria-live="polite"><div class="agent-inspector-heading"><div class="agent-workspace-identity"><div class="agent-avatar">${escapeHtml(agent.short)}</div><div><p class="eyebrow">AGENT DETAILS</p><h3>${escapeHtml(agent.label)}</h3><p>${escapeHtml(agent.purpose)}</p></div></div><div class="agent-inspector-actions">${statusPill(stateLabel, status === "complete" ? "good" : status === "failed" || status === "attention" ? "bad" : status === "active" ? "good" : "neutral", false)}<button id="agent-inspector-close" type="button" class="secondary">Close</button></div></div><div class="agent-inspector-skill"><div class="agent-inspector-skill-heading"><div><span>Skill used in this run</span><strong>${escapeHtml(skillStatus)}</strong></div><span class="agent-skill-binding">${escapeHtml(skillTrace.appliedVia)}</span></div><div class="agent-tool-list">${skillChips}</div><small>${escapeHtml(skillTrace.note)}</small><div class="agent-inspector-section-heading"><div><span>Skill instructions</span><strong>${escapeHtml(skillTrace.definitionSource || "reference guidance")}</strong></div></div><div class="agent-skill-definitions">${skillDefinitions}</div></div><div class="agent-inspector-facts"><div><span>Harness session</span><strong>${escapeHtml(session.session_id || "Not started")}</strong><small>${escapeHtml(tasks.length)} persisted task${tasks.length === 1 ? "" : "s"} · ${escapeHtml(iterations)} iteration${iterations === 1 ? "" : "s"}</small></div><div><span>Run activity</span><strong>${escapeHtml(calls.length || observedTools.length)} tool call${(calls.length || observedTools.length) === 1 ? "" : "s"}</strong><small>${escapeHtml(task?.status ? pretty(task.status) : "No task record yet")}</small></div><div><span>Skill authority</span><strong>${escapeHtml(skillTrace.authority)}</strong><small>guidance only; it cannot authorize a simulator change</small></div><div><span>Authority boundary</span><strong>Read-only</strong><small>${escapeHtml(authority)}</small></div></div><div class="agent-inspector-columns"><div><span>Tools called in this run</span><div class="agent-tool-list">${chips(observedTools, "No tool calls persisted yet")}</div></div><div><span>Harness skill attachments</span><div class="agent-tool-list">${attachmentChips}</div><small class="agent-inspector-note">Configured role skills: ${escapeHtml(configuredAgentSkills(agent).join(" · ") || "none")}</small></div></div>${promptMarkup}</section>`;
}

function activityEntries(data) {
  const entries = [];
  const cp = checkpoint(data);
  const stall = investigationStall(data);
  const stage = cp.stage || "initialize";
  for (const stage of cp.completed_stages || []) {
    entries.push({ kind: "stage", title: `Completed · ${stageLabel(stage)}`, detail: "This part of the investigation is complete.", time: null, status: "complete" });
  }
  const toolEvents = orderedToolEvents(data);
  for (const event of toolEvents) {
    entries.push({ kind: "tool", title: businessToolLabel(event.tool_name), detail: activityResultDetail(event, data), tool: event.tool_name, agent: activityAgentForEvent(event, data), time: event.observed_at, status: event.status || "ok", evidence: event.evidence_ids || [] });
  }
  if (stall) {
    entries.push({ kind: "attention", title: stall.kind === "handoff" ? "Evidence checks completed; handoff not saved" : "Evidence collection has not returned", detail: stall.kind === "handoff" ? "The next business step is to confirm the likely cause. Resume the investigation to continue." : `No specialist result has been saved after ${elapsedSince(data.run?.created_at)}. Retry evidence collection to move forward.`, time: data.run?.created_at, status: "awaiting" });
  }
  if (cp.stage) {
    const currentDetail = stall
        ? (stall.kind === "handoff" ? "The run is waiting to confirm the likely cause." : "The run is waiting for the first specialist result.")
        : workflowNextStep(cp.stage);
    entries.push({ kind: "current", title: `Current step · ${stageLabel(cp.stage)}`, detail: currentDetail, time: null, status: isLive(data) ? "active" : "pending" });
  }
  return entries.slice(-18).reverse();
}

function renderActivityFeed(data, limit = 8) {
  const entries = activityEntries(data).slice(0, limit);
  if (!entries.length) return '<p class="empty-state">The first checkpoint will appear here when the run starts.</p>';
  return `<div class="activity-feed">${entries.map((entry) => `<div class="activity-row ${entry.kind} ${toneForStatus(entry.status)}"><span class="activity-marker">${entry.kind === "tool" ? "↗" : entry.kind === "attention" ? "!" : entry.kind === "current" ? "•" : "✓"}</span><div><strong>${escapeHtml(entry.title)}</strong><small>${escapeHtml(entry.detail)}</small>${entry.agent || entry.source || entry.tool ? `<span class="activity-meta">${entry.agent ? `<span class="activity-agent">${escapeHtml(agentLabel(entry.agent))}</span>` : entry.source ? `<span class="activity-agent">${escapeHtml(entry.source)}</span>` : ""}${entry.tool ? `<span class="activity-tool">${escapeHtml(displayToolName(entry.tool))}</span>` : ""}</span>` : ""}${entry.evidence?.length ? `<span class="evidence-inline">${entry.evidence.slice(0, 3).map((id) => `<code>${escapeHtml(id)}</code>`).join("")}</span>` : ""}</div><time>${formatTime(entry.time)}</time></div>`).join("")}</div>`;
}

function activityFooterText(data) {
  const stage = checkpoint(data).stage;
  if (stage === "awaiting_follow_up") return "Read-only checks are closing the evidence gap. No service change is being made.";
  if (isAnalysisCompleteStage(stage)) return "Analysis is complete. Review the evidence workspace and agent traces.";
  if (isLive(data)) return "Watching for new agent work and tool observations.";
  return "No active work is running. Use the highlighted action to continue.";
}

function renderEvidencePulse(data) {
  const ev = evidence(data);
  const latestFinding = (ev.findings || [])[ev.findings.length - 1];
  const latestHypothesis = (ev.hypotheses || [])[ev.hypotheses.length - 1];
  return `<div class="evidence-pulse"><div class="evidence-counts"><div><strong>${ev.findings?.length || 0}</strong><span>findings</span></div><div><strong>${ev.hypotheses?.length || 0}</strong><span>hypotheses</span></div><div><strong>${ev.evidence?.length || 0}</strong><span>sources</span></div><div><strong>${allToolCount(data)}</strong><span>tool calls</span></div></div>${latestFinding ? `<div class="claim-preview fact"><span>Latest fact</span><strong>${escapeHtml(latestFinding.statement)}</strong><small>${escapeHtml((latestFinding.evidence_ids || []).join(" · "))}</small></div>` : ""}${latestHypothesis ? `<div class="claim-preview hypothesis"><span>Latest hypothesis</span><strong>${escapeHtml(latestHypothesis.statement)}</strong><small>confidence ${formatNumber(Number(latestHypothesis.confidence || 0) * 100, "%")}</small></div>` : ""}</div>`;
}

function traceQuestion(agentId, toolName = "", data = {}) {
  const name = String(toolName || "").replace(/^.*___/, "").toLowerCase();
  if (name.includes("service_health") || name.includes("cloudwatch") || name.includes("metric") || name.includes("alarm") || name.includes("trace") || name.includes("log")) {
    return "Is the service still under pressure, and when did it start?";
  }
  if (name.includes("deployment") || name.includes("configuration") || name.includes("config") || name.includes("runbook")) {
    return "Did a recent change precede the customer impact?";
  }
  if (name.includes("database") || name.includes("queue") || name.includes("order") || name.includes("customer")) {
    return "Which customers, orders, or business processes are affected?";
  }
  if (agentId === "orchestrator") {
    return "Which causal explanation best fits all of the evidence?";
  }
  const stage = checkpoint(data).stage;
  if (stage === "reconcile_evidence") return "Which explanation best connects the collected facts?";
  if (stage === "awaiting_follow_up") return "What evidence is still needed to close the gap?";
  return agentId === "change-agent"
    ? "Which recent change needs to be compared with the incident?"
    : agentId === "data-agent"
      ? "What is the customer and order impact?"
      : "Which service signal should be checked next?";
}

function traceToolReason(toolName = "", agentId = "") {
  const name = String(toolName || "").replace(/^.*___/, "").toLowerCase();
  const reasons = {
    get_incident_context: "Establish the incident scope and service context.",
    get_shared_evidence: "Compare the facts already collected by the team.",
    get_service_health: "Check whether the service remains under pressure.",
    query_cloudwatch_metrics: "Measure the timing and severity of the service symptoms.",
    compare_metric_windows: "Compare normal behavior with the incident window.",
    search_application_logs: "Find concrete error and timeout evidence.",
    get_service_traces: "Follow the affected request path for failure evidence.",
    get_active_alarms: "Confirm which monitored signals are currently firing.",
    list_recent_deployments: "Find changes that happened before customer impact.",
    get_deployment_details: "Inspect the release most closely tied to the incident.",
    compare_deployment_versions: "Compare the new release with the prior version.",
    get_configuration_changes: "Identify configuration differences that could explain the symptoms.",
    search_runbooks: "Compare the observed pattern with known guidance.",
    get_database_metrics: "Check whether database pressure explains the delay.",
    get_queue_metrics: "Measure the backlog and age of customer work.",
    query_order_records: "Inspect affected orders without changing them.",
    get_order_processing_history: "Check whether orders are progressing or stalled.",
    calculate_customer_impact: "Quantify the customers and orders affected.",
  };
  return reasons[name] || (agentId === "orchestrator" ? "Reconcile specialist evidence before analysis." : "Collect the next read-only observation.");
}

function traceNextQuestion(agentId, toolName = "", data = {}) {
  const name = String(toolName || "").replace(/^.*___/, "").toLowerCase();
  if (name.includes("deployment") || name.includes("configuration") || name.includes("config")) {
    return "Which change differs from the prior known-good state?";
  }
  if (name.includes("database") || name.includes("queue") || name.includes("order") || name.includes("customer")) {
    return "Are delayed orders and affected customers increasing?";
  }
  if (name.includes("metric") || name.includes("health") || name.includes("alarm") || name.includes("trace") || name.includes("log")) {
    return "Does the timing line up with the reported incident?";
  }
  if (agentId === "orchestrator") return "Does the combined evidence support the working explanation?";
  return traceQuestion(agentId, toolName, data);
}

function tracePromptSummary(task, fallback) {
  const raw = String(task?.session?.prompt_trace?.user_prompt || "");
  const objective = raw.match(/Investigation objective:\s*([^\n]+)/i)?.[1]?.trim();
  return objective || fallback;
}

function agentTraceEvents(data, agentId) {
  const events = orderedToolEvents(data).filter((event) => {
    const role = String(event?.role || event?.agent || activityAgentForEvent(event, data) || "");
    return role === agentId;
  });
  if (events.length) return events;
  return modelToolCalls(data)
    .filter((call) => call.role === agentId)
    .map((call) => ({
      tool_name: call.name,
      data: null,
      evidence_ids: [],
      status: "pending",
      input: call.input,
      role: agentId,
    }));
}

function traceResult(event, data) {
  if (!event) return "Waiting for the first result.";
  if (event.status && event.status !== "ok" && event.status !== "pending") return `${pretty(event.status)} — the check needs attention.`;
  const facts = responseFacts(event, data).slice(0, 2);
  if (facts.length) return facts.join(" · ");
  if ((event.evidence_ids || []).length) return `${event.evidence_ids.length} supporting record${event.evidence_ids.length === 1 ? "" : "s"} saved.`;
  return event.status === "pending" ? "Waiting for the authoritative observation." : "Structured result saved.";
}

function traceStatus(agent, data) {
  const status = agentState(agent, data);
  return {
    className: status,
    label: status === "complete" ? "Complete" : status === "active" ? "Working now" : status === "failed" || status === "attention" ? "Needs attention" : "Waiting",
  };
}

function renderTraceHistory(data, agent, events) {
  if (!events.length) return '<p class="trace-empty">No tool call has been recorded yet.</p>';
  return `<details class="trace-history"><summary>View ${events.length} check${events.length === 1 ? "" : "s"} and result${events.length === 1 ? "" : "s"}</summary><div class="trace-history-list">${events.slice().reverse().map((event, index) => {
    const toolName = String(event.tool_name || "").replace(/^.*___/, "");
    const question = traceQuestion(agent.id, toolName, data);
    const result = traceResult(event, data);
    return `<div class="trace-history-row"><span class="trace-history-index">${String(events.length - index).padStart(2, "0")}</span><div><strong>${escapeHtml(displayToolName(toolName))}</strong><small>${escapeHtml(question)}</small><p>${escapeHtml(result)}</p>${(event.evidence_ids || []).length ? `<code>${escapeHtml(event.evidence_ids.join(" · "))}</code>` : ""}</div><details class="trace-input"><summary>Input</summary><pre>${escapeHtml(safeJson(event.input || {}))}</pre></details></div>`;
  }).join("")}</div></details>`;
}

function renderTraceLane(data, agent) {
  const tasks = taskResults(data).filter((task) => task.role === agent.id);
  const task = tasks[tasks.length - 1];
  const events = agentTraceEvents(data, agent.id);
  const latest = events[events.length - 1];
  const toolName = String(latest?.tool_name || "").replace(/^.*___/, "");
  const status = traceStatus(agent, data);
  const question = tracePromptSummary(task, traceQuestion(agent.id, toolName, data));
  const nextQuestion = traceNextQuestion(agent.id, toolName, data);
  const reason = traceToolReason(toolName, agent.id);
  const result = traceResult(latest, data);
  const iterationCount = Number(task?.session?.metrics?.iteration_count || task?.session?.metrics?.turn_count || events.length || 0);
  const active = status.className === "active" || status.className === "attention";
  const selected = state.selectedAgentId === agent.id;
  return `<article class="trace-lane ${agent.accent} ${status.className} ${selected ? "selected" : ""}" data-agent-id="${escapeHtml(agent.id)}" tabindex="0" role="button" aria-pressed="${selected ? "true" : "false"}" aria-label="Inspect ${escapeHtml(agent.label)}"><div class="trace-lane-heading"><div class="trace-agent-identity"><span class="trace-agent-avatar">${escapeHtml(agent.short)}</span><div><strong>${escapeHtml(agent.label)}</strong><small>${escapeHtml(agent.purpose)}</small></div></div><span class="trace-agent-status ${status.className}">${escapeHtml(status.label)}</span></div><div class="trace-flow-label"><span>LIVE BUSINESS LOOP</span><small>question ↔ evidence ↔ next question</small></div><div class="trace-loop-graphic" aria-label="${escapeHtml(agent.label)} iterative agent loop"><div class="trace-bubble trace-bubble-question"><span>1 · BUSINESS QUESTION</span><strong>${escapeHtml(question)}</strong><small>${escapeHtml(reason)}</small></div><span class="trace-loop-arrow trace-loop-arrow-top" aria-hidden="true">→</span><div class="trace-bubble trace-bubble-tool ${active ? "active" : ""}"><span>2 · TOOL CALLED</span><strong>${escapeHtml(toolName ? displayToolName(toolName) : "Waiting for a tool")}</strong><code>${escapeHtml(toolName || "—")}</code></div><span class="trace-loop-arrow trace-loop-arrow-right" aria-hidden="true">↓</span><div class="trace-bubble trace-bubble-found"><span>3 · WHAT IT FOUND</span><strong>${escapeHtml(result)}</strong><small>${latest?.evidence_ids?.length ? `${latest.evidence_ids.length} evidence record${latest.evidence_ids.length === 1 ? "" : "s"}` : "read-only observation"}</small></div><span class="trace-loop-arrow trace-loop-arrow-bottom" aria-hidden="true">←</span><div class="trace-bubble trace-bubble-next"><span>4 · NEXT QUESTION</span><strong>${escapeHtml(nextQuestion)}</strong><small>the next turn returns to the business question</small></div><span class="trace-loop-arrow trace-loop-arrow-left" aria-hidden="true">↑</span><div class="trace-loop-center ${active ? "active" : ""}"><span>AGENT LOOP</span><strong>${escapeHtml(iterationCount || "—")}</strong><small>${iterationCount === 1 ? "turn" : "turns"}</small></div></div><div class="trace-hover-card"><span>Agent prompt</span><strong>${escapeHtml(question)}</strong><span>Why this call</span><strong>${escapeHtml(reason)}</strong><span>Tool result</span><strong>${escapeHtml(result)}</strong></div>${renderTraceHistory(data, agent, events)}</article>`;
}

function renderTraceSynthesis(data) {
  const agent = agentDefinition("orchestrator");
  const tasks = taskResults(data).filter((task) => task.role === agent.id);
  const task = tasks[tasks.length - 1];
  const events = agentTraceEvents(data, agent.id);
  const latest = events[events.length - 1];
  const toolName = String(latest?.tool_name || "").replace(/^.*___/, "");
  const status = traceStatus(agent, data);
  const selected = state.selectedAgentId === agent.id;
  return `<article class="trace-synthesis ${status.className} ${selected ? "selected" : ""}" data-agent-id="${escapeHtml(agent.id)}" tabindex="0" role="button" aria-pressed="${selected ? "true" : "false"}" aria-label="Inspect ${escapeHtml(agent.label)}"><div class="trace-synthesis-copy"><span class="trace-synthesis-kicker">ORCHESTRATOR RECONCILIATION</span><strong>Compare the facts and identify the likely cause.</strong><small>${escapeHtml(status.label)} · ${escapeHtml(tracePromptSummary(task, traceQuestion(agent.id, toolName, data)))}</small></div><div class="trace-synthesis-outcome"><span>OUTCOME</span><strong>${isAnalysisCompleteStage(checkpoint(data).stage) ? "Analysis ready" : "Likely cause under review"}</strong></div></article>`;
}

function renderTraceDetails(data) {
  const selected = state.selectedAgentId ? agentDefinition(state.selectedAgentId) : null;
  const ev = evidence(data);
  const findings = ev.findings || [];
  const hypotheses = ev.hypotheses || [];
  const evaluation = data.evaluation;
  return `<section class="trace-details"><details class="trace-detail-section" open><summary><span><strong>Findings and evidence</strong><small>What the investigation has actually established</small></span><b>${findings.length + hypotheses.length} claims · ${ev.evidence?.length || 0} sources</b></summary><div class="trace-evidence-summary">${renderEvidencePulse(data)}<div class="claim-columns"><div><p class="trace-detail-label">Observed findings</p><div class="claim-list">${findings.length ? findings.map((finding) => claimCard(finding, "finding")).join("") : '<p class="empty-state">No findings yet.</p>'}</div></div><div><p class="trace-detail-label">Hypotheses</p><div class="claim-list">${hypotheses.length ? hypotheses.map((hypothesis) => claimCard(hypothesis, "hypothesis")).join("") : '<p class="empty-state">No hypotheses yet.</p>'}</div></div></div></div></details><details class="trace-detail-section"><summary><span><strong>Analysis quality</strong><small>Independent read-only checks</small></span><b>${evaluation ? `${Math.round(Number(evaluation.score || 0) * 100)}%` : "pending"}</b></summary><div class="trace-evaluation-detail">${renderEvaluationView(data)}</div></details>${selected ? `<div class="trace-selected-inspector">${renderAgentInspector(data, selected)}</div>` : '<p class="trace-selection-hint">Hover over a lane for a quick summary, or click an agent to inspect its skill, prompt, tools, and evidence.</p>'}</section>`;
}

function renderTraceAlert(data) {
  const cp = checkpoint(data);
  const failures = Array.isArray(cp.metadata?.follow_up_failures) ? cp.metadata.follow_up_failures : [];
  const hasFailure = Boolean(state.followUpError || failures.length || (state.noticeTone === "bad" && state.notice));
  if (!hasFailure) return "";
  const message = state.followUpError
    || (failures.length ? "One or more read-only evidence checks did not return a usable result. Automatic retries may continue." : state.notice)
    || "A read-only evidence check needs attention.";
  const failureRows = failures.map((failure) => `<div><strong>${escapeHtml(agentLabel(failure.role || "specialist"))}</strong><span>${escapeHtml(failure.error || "This check did not return a usable result.")}</span></div>`).join("");
  return `<div class="trace-inline-alert" role="alert"><div><span class="trace-inline-alert-kicker">READ-ONLY CHECK NEEDS ATTENTION</span><strong>Analysis is waiting on evidence, not operator approval.</strong><p>${escapeHtml(message)}</p></div>${failureRows ? `<div class="trace-inline-alert-details">${failureRows}</div>` : ""}<small>No service change was made. The agent can retry the check automatically.</small></div>`;
}

function renderBusinessTrace(data) {
  const health = data.health || {};
  const status = businessStatus(data);
  const stage = checkpoint(data).stage || "initialize";
  const specialists = AGENTS.filter((agent) => agent.id !== "orchestrator");
  const selected = state.selectedAgentId ? agentDefinition(state.selectedAgentId) : null;
  return `<section class="panel business-trace-panel"><div class="section-heading business-trace-heading"><div><p class="eyebrow">LIVE INVESTIGATION TRACE</p><h2>${escapeHtml(status.message)}</h2><p class="muted">Each lane is a live four-bubble business loop: question, tool call, finding, and the next question.</p></div><div class="trace-live-summary">${statusPill(status.label, status.tone)}<span>${escapeHtml(stageLabel(stage))}</span></div></div>${renderTraceAlert(data)}<div class="trace-summary-row"><div><span>Customer impact</span><strong>${escapeHtml(businessImpact(health))}</strong><small>${escapeHtml(formatNumber(health.queue_depth))} queued · ${escapeHtml(formatNumber(health.order_processing_delay_seconds, " s"))} order delay</small></div><div><span>Current business question</span><strong>${escapeHtml(traceQuestion(activeAgent(data).split(" · ")[0], "", data))}</strong><small>read-only checks continue until the evidence is sufficient</small></div><div><span>Analysis boundary</span><strong>Nothing changes</strong><small>the simulator remains at its original snapshot</small></div></div><div class="trace-graphic-legend"><span class="trace-legend-title">FOUR-STEP BUSINESS LOOP</span><span>Hover for the quick read</span><span>Click a lane for the full agent record</span><span class="trace-legend-active"><i></i>live step</span></div><div class="trace-lanes">${specialists.map((agent) => renderTraceLane(data, agent)).join("")}</div><div class="trace-synthesis-arrow">↓</div>${renderTraceSynthesis(data)}${selected ? `<p class="trace-selected-label">Selected: ${escapeHtml(selected.label)}</p>` : ""}</section>`;
}

function renderOverview(data) {
  return `<div class="view-stack">
    ${renderBusinessTrace(data)}
    ${renderTraceDetails(data)}
  </div>`;
}

function renderAgentsView(data) {
  return `<div class="view-stack"><section class="panel"><div class="section-heading"><div><p class="eyebrow">AGENT COLLABORATION</p><h2>Four role-scoped sessions around one incident</h2><p class="muted">Three read-only specialist sessions can run in parallel. The orchestrator reconciles their structured outputs into a final analysis.</p></div><span class="topology-count">${taskResults(data).length} task records</span></div>${renderAgentRoster(data)}</section>${renderAgentWorkspaces(data, true)}<section class="panel topology-panel"><div class="section-heading"><div><p class="eyebrow">DELEGATION GRAPH</p><h2>Shared objective → specialist evidence → synthesis</h2></div></div><div class="delegation-graph"><div class="delegation-node root"><span>SF</span><strong>Durable workflow</strong><small>${escapeHtml(stageLabel(checkpoint(data).stage))}</small></div><div class="delegation-connector"></div><div class="delegation-branches"><div class="branch-card cyan"><strong>Telemetry</strong><small>metrics + logs</small><code>${taskResults(data).some((t) => t.role === "telemetry-agent") ? "completed" : "parallel branch"}</code></div><div class="branch-card blue"><strong>Change analysis</strong><small>deployments + config</small><code>${taskResults(data).some((t) => t.role === "change-agent") ? "completed" : "parallel branch"}</code></div><div class="branch-card amber"><strong>Data + impact</strong><small>DB + queue + orders</small><code>${taskResults(data).some((t) => t.role === "data-agent") ? "completed" : "parallel branch"}</code></div></div><div class="delegation-connector down"></div><div class="delegation-node synthesis"><span>◎</span><strong>Orchestrator reconciliation</strong><small>conflict check + evidence gap detection</small></div></div></section><section class="panel"><div class="section-heading"><div><p class="eyebrow">ROLE BOUNDARIES</p><h2>Why specialization matters</h2></div></div><div class="role-table"><div class="role-table-head"><span>Agent</span><span>Primary question</span><span>Authority boundary</span></div>${AGENTS.map((agent) => `<div class="role-row"><strong>${escapeHtml(agent.label)}</strong><span>${escapeHtml(agent.purpose)}</span><code>read-only</code></div>`).join("")}</div></section></div>`;
}

function renderToolRows(data, limit = 40) {
  const events = orderedToolEvents(data).slice(-limit).reverse();
  if (!events.length) return '<p class="empty-state">No Gateway observations have been persisted yet.</p>';
  return `<div class="tool-table"><div class="tool-table-head"><span>Observed</span><span>Gateway tool</span><span>Status</span><span>Evidence</span></div>${events.map((event) => `<div class="tool-row"><time>${formatTime(event.observed_at)}</time><strong>${escapeHtml(displayToolName(event.tool_name))}</strong>${statusPill(pretty(event.status || "ok"), toneForStatus(event.status), false)}<span class="evidence-links">${(event.evidence_ids || []).slice(0, 4).map((id) => `<code>${escapeHtml(id)}</code>`).join("") || "—"}</span></div>`).join("")}</div>`;
}

function renderToolsView(data) {
  const calls = modelToolCalls(data);
  return `<div class="view-stack"><section class="panel"><div class="section-heading"><div><p class="eyebrow">TOOL CALL INSPECTOR</p><h2>Every call crosses the Gateway boundary</h2><p class="muted">The console shows tool names, bounded inputs, policy outcomes, and evidence IDs. It does not display hidden model reasoning.</p></div><span class="topology-count">${allToolCount(data)} observations</span></div>${renderHarnessLoop(data)}</section><section class="panel"><div class="section-heading"><div><p class="eyebrow">AUTHORITATIVE OBSERVATIONS</p><h2>Gateway response stream</h2></div><span class="muted">${calls.length} Harness tool selections captured</span></div>${renderToolRows(data)}</section><section class="panel"><div class="section-heading"><div><p class="eyebrow">MODEL SELECTIONS</p><h2>Bounded inputs attached to Harness calls</h2></div></div><div class="selection-list">${calls.length ? calls.slice(-20).reverse().map((call, index) => `<details class="selection-row"${index === 0 ? " open" : ""}><summary><span class="selection-index">${String(calls.length - index).padStart(2, "0")}</span><strong>${escapeHtml(displayToolName(call.name))}</strong><span>${escapeHtml(call.role)}</span></summary><div class="selection-body">${jsonDetails("input", call.input, true)}</div></details>`).join("") : '<p class="empty-state">Harness selections will appear after the first model turn.</p>'}</div></section></div>`;
}

function claimCard(claim, kind) {
  const ids = claim.evidence_ids || [];
  return `<article class="claim-card ${kind}"><div class="claim-heading"><span>${escapeHtml(pretty(claim.claim_type || kind))}</span><strong>${formatNumber(Number(claim.confidence || 0) * 100, "%")}</strong></div><p>${escapeHtml(claim.statement || claim.description || "No statement")}</p><div class="claim-sources">${ids.map((id) => `<code>${escapeHtml(id)}</code>`).join("") || "No source IDs"}</div><small>${escapeHtml(claim.agent || claim.created_at || "structured workspace record")}</small></article>`;
}

function renderEvidenceView(data) {
  const ev = evidence(data);
  return `<div class="view-stack"><section class="panel"><div class="section-heading"><div><p class="eyebrow">SHARED EVIDENCE WORKSPACE</p><h2>Facts, relationships, and hypotheses</h2><p class="muted">A claim is accepted only when its evidence IDs resolve to records in this scenario and tenant.</p></div>${statusPill(`${ev.evidence?.length || 0} source records`, "good", false)}</div>${renderEvidencePulse(data)}</section><div class="claim-columns"><section class="panel"><div class="section-heading"><div><p class="eyebrow">FINDINGS</p><h2>Observed claims</h2></div><span class="muted">${ev.findings?.length || 0}</span></div><div class="claim-list">${ev.findings?.length ? ev.findings.map((finding) => claimCard(finding, "finding")).join("") : '<p class="empty-state">No findings yet.</p>'}</div></section><section class="panel"><div class="section-heading"><div><p class="eyebrow">HYPOTHESES</p><h2>Interpretations under review</h2></div><span class="muted">${ev.hypotheses?.length || 0}</span></div><div class="claim-list">${ev.hypotheses?.length ? ev.hypotheses.map((hypothesis) => claimCard(hypothesis, "hypothesis")).join("") : '<p class="empty-state">No hypotheses yet.</p>'}</div></section></div><section class="panel"><div class="section-heading"><div><p class="eyebrow">UNRESOLVED QUESTIONS</p><h2>Gaps that can trigger follow-up</h2></div></div>${ev.unresolved_questions?.length ? `<div class="question-list">${ev.unresolved_questions.map((item) => `<div class="question-row"><span>?</span><div><strong>${escapeHtml(item.question || item)}</strong><small>requested by ${escapeHtml(item.requested_by || "agent")}</small></div></div>`).join("")}</div>` : '<p class="empty-state">No unresolved questions recorded.</p>'}</section></div>`;
}

function renderEvaluationView(data) {
  const evaluation = data.evaluation;
  if (!evaluation) return `<div class="view-stack"><section class="panel empty-panel"><p class="eyebrow">ANALYSIS EVALUATION</p><h2>Evaluation is available when analysis completes.</h2><p class="muted">The evaluator checks evidence citations, the hidden-state boundary, causal coverage, and the read-only execution boundary.</p></section></div>`;
  const checks = Object.entries(evaluation.checks || {});
  const passed = checks.filter(([, check]) => check?.passed).length;
  return `<div class="view-stack"><section class="panel evaluation-hero"><div class="section-heading"><div><p class="eyebrow">ANALYSIS EVALUATION</p><h2>Does the analysis deserve trust?</h2><p class="muted">Deterministic checks are evaluated independently from the agent findings.</p></div>${statusPill(`${evaluation.passed ? "passed" : "needs review"} · ${Math.round(Number(evaluation.score || 0) * 100)}%`, evaluation.passed ? "good" : "bad", false)}</div><div class="score-grid"><div><strong>${escapeHtml(Math.round(Number(evaluation.score || 0) * 100))}%</strong><span>analysis score</span></div><div><strong>${escapeHtml(passed)}/${escapeHtml(checks.length)}</strong><span>checks passed</span></div><div><strong>${escapeHtml(allToolCount(data))}</strong><span>tool calls</span></div><div><strong>0</strong><span>state changes</span></div></div></section><section class="panel"><div class="section-heading"><div><p class="eyebrow">READ-ONLY CHECKS</p><h2>Evaluation details</h2></div><span class="muted">${passed}/${checks.length} passed</span></div><div class="assertion-list">${checks.length ? checks.map(([name, check]) => `<div class="assertion-row ${check?.passed ? "passed" : "failed"}"><span>${check?.passed ? "✓" : "!"}</span><strong>${escapeHtml(pretty(name))}</strong><p>${escapeHtml(typeof check?.details === "string" ? check.details : safeJson(check?.details || ""))}</p></div>`).join("") : '<p class="empty-state">No check details returned.</p>'}</div></section>${jsonDetails("evaluation payload", evaluation, false)}</div>`;
}

function render() {
  updateConnection(state.data);
  const data = state.data;
  if (!data) {
    $("empty").classList.remove("hidden");
    $("workspace").classList.add("hidden");
    $("run-status-summary").textContent = state.runId ? "Loading run…" : "No run selected";
    if (state.notice) setNotice(state.notice, state.noticeTone);
    return;
  }
  $("empty").classList.add("hidden");
  $("workspace").classList.remove("hidden");
  renderRunBanner(data);
  const body = renderOverview(data);
  $("view").innerHTML = body;
  bindDynamicActions();
  if (state.notice) setNotice(state.notice, state.noticeTone);
  else setNotice("");
}

async function refresh({ silent = false } = {}) {
  if (!state.runId || state.refreshing) return;
  state.refreshing = true;
  try {
    const payload = await api(`/api/runs/${encodeURIComponent(state.runId)}`);
    const nextSignature = renderSignature(payload);
    const dataChanged = nextSignature !== state.lastRenderSignature;
    state.data = payload;
    state.lastRefresh = new Date().toISOString();
    if (!silent) state.notice = "";
    state.lastRenderSignature = nextSignature;
    if (!document.hidden && (!silent || dataChanged)) render();
    ensurePolling();
    maybeRunAutomaticFollowUp(state.data);
  } catch (error) {
    if (!silent || !state.data) {
      state.notice = error.message;
      state.noticeTone = "bad";
      render();
    }
  } finally {
    state.refreshing = false;
  }
}

function ensurePolling() {
  if (document.hidden) {
    stopPolling();
    return;
  }
  if (isLive(state.data) && !state.pollTimer) {
    state.pollTimer = window.setInterval(() => refresh({ silent: true }), 2500);
  }
  if (!isLive(state.data) && state.pollTimer) {
    window.clearInterval(state.pollTimer);
    state.pollTimer = null;
  }
}

function stopPolling() {
  if (state.pollTimer) window.clearInterval(state.pollTimer);
  state.pollTimer = null;
  stopProgressTimer();
}

document.addEventListener("visibilitychange", () => {
  if (document.hidden) {
    stopPolling();
    return;
  }
  if (state.data) render();
  ensurePolling();
  refresh({ silent: true });
});
window.addEventListener("pagehide", stopPolling);

async function launch() {
  if (state.busy) return;
  const scenario = $("scenario").value;
  const objective = $("objective").value.trim() || `Investigate ${scenario}`;
  const runId = `console-${scenario.toLowerCase()}-${Date.now()}`;
  state.busy = "launch";
  state.runId = runId;
  state.lastRenderSignature = "";
  state.view = "overview";
  state.selectedAgentId = null;
  state.followUpError = "";
  state.followUpAutoKey = "";
  state.followUpAutoAttempts = 0;
  state.notice = "Creating the run and opening the live trace…";
  state.noticeTone = "";
  render();
  try {
    const created = await api("/api/runs", { method: "POST", body: JSON.stringify({ scenario_id: scenario, run_id: runId }) });
    state.data = { ...created, checkpoint: { stage: "initialize", version: 0, completed_stages: [], task_results: {}, metadata: {} }, evidence: EMPTY_EVIDENCE };
    state.busy = null;
    state.investigateInFlight = true;
    state.notice = "Investigation dispatched. Watching Step Functions and Harness records as they arrive…";
    render();
    ensurePolling();
    try {
      await api(`/api/runs/${encodeURIComponent(runId)}/investigate`, { method: "POST", body: JSON.stringify({ objective }) });
      state.notice = "Investigation stage completed. Review the live trace and evidence workspace.";
      state.noticeTone = "";
    } catch (error) {
      state.notice = error.message;
      state.noticeTone = "bad";
    } finally {
      state.investigateInFlight = false;
      await refresh({ silent: true });
      ensurePolling();
    }
  } catch (error) {
    state.busy = null;
    state.investigateInFlight = false;
    state.notice = error.message;
    state.noticeTone = "bad";
    render();
  }
}

async function retryInvestigation() {
  if (!state.runId || state.busy) return;
  const metadata = checkpoint(state.data).metadata || {};
  const objective = String(metadata.objective || `Investigate ${state.data?.run?.scenario_id || state.runId}`);
  const stall = investigationStall(state.data);
  state.busy = "investigate";
  state.investigateInFlight = true;
  state.notice = stall?.kind === "handoff"
    ? "Resuming the investigation from the evidence already collected…"
    : "Retrying evidence collection. Waiting for the first specialist result…";
  state.noticeTone = "";
  render();
  ensurePolling();
  try {
    const result = await api(`/api/runs/${encodeURIComponent(state.runId)}/investigate`, { method: "POST", body: JSON.stringify({ objective }) });
    state.notice = result.status === "needs_follow_up"
      ? "Evidence collection completed, but the team found an open question before analysis can complete."
      : result.status === "retryable_failure"
        ? "The evidence checks could not be handed off. Resume the investigation to try again."
      : "Read-only analysis completed. Review the findings, hypotheses, and evidence.";
    state.noticeTone = "";
  } catch (error) {
    state.notice = error.message;
    state.noticeTone = "bad";
  } finally {
    state.investigateInFlight = false;
    await refresh({ silent: true });
    state.busy = null;
    ensurePolling();
    render();
  }
}

async function runFollowUp(automatic = false) {
  if (!state.runId || state.busy) return;
  state.followUpError = "";
  state.followUpInFlight = true;
  state.followUpStartedAt = Date.now();
  state.followUpInitialToolCount = allToolCount(state.data);
  state.busy = "follow-up";
  state.notice = automatic
    ? "Closing the evidence gap with read-only checks…"
    : "Retrying the read-only evidence checks…";
  state.noticeTone = "";
  render();
  startProgressTimer();
  ensurePolling();
  try {
    const result = await api(`/api/runs/${encodeURIComponent(state.runId)}/follow-up`, { method: "POST", body: JSON.stringify({}) });
    state.notice = result.status === "needs_follow_up"
      ? (result.message || "Some evidence checks remain open. The completed checks are saved; retry the remaining read-only checks to finish analysis.")
      : "Evidence checks completed. The analysis is ready for review.";
    await refresh({ silent: true });
  } catch (error) {
    state.followUpError = error.message || "The evidence checks could not save their checkpoint.";
    state.notice = "Evidence checks did not update the saved workflow. See the reason in the investigation panel.";
    state.noticeTone = "bad";
    // The request may have failed after the server saved partial work.
    // Reconcile with the authoritative checkpoint before retrying.
    await refresh({ silent: true });
  } finally {
    state.followUpInFlight = false;
    state.followUpStartedAt = null;
    state.followUpInitialToolCount = 0;
    state.busy = null;
    stopProgressTimer();
    ensurePolling();
    render();
    if (automatic && checkpoint(state.data).stage === "awaiting_follow_up") {
      state.followUpAutoKey = "";
      const retryNumber = Math.max(0, state.followUpAutoAttempts - 1);
      const retryDelay = Math.min(
        FOLLOW_UP_RETRY_MAX_DELAY_MS,
        FOLLOW_UP_RETRY_BASE_DELAY_MS * (2 ** Math.min(retryNumber, 5)),
      );
      const runId = state.runId;
      window.setTimeout(() => {
        if (document.hidden || state.runId !== runId) return;
        maybeRunAutomaticFollowUp(state.data);
      }, retryDelay);
    }
  }
}

function bindDynamicActions() {
  const toggleWorkflow = () => {
    state.workflowExpanded = !state.workflowExpanded;
    render();
  };
  $("workflow-toggle")?.addEventListener("click", toggleWorkflow);
  document.querySelector("[data-workflow-panel]")?.addEventListener("click", (event) => {
    if (event.target.closest("button, a, input, select, details, summary, [data-workflow-details]")) return;
    toggleWorkflow();
  });
  $("retry-investigation")?.addEventListener("click", retryInvestigation);
  document.querySelectorAll(".agent-card[data-agent-id]").forEach((card) => card.addEventListener("click", () => {
    state.selectedAgentId = card.dataset.agentId || null;
    render();
    window.setTimeout(() => $("agent-inspector")?.scrollIntoView({ block: "nearest", behavior: "smooth" }), 0);
  }));
  document.querySelectorAll(".trace-lane[data-agent-id], .trace-synthesis[data-agent-id]").forEach((lane) => {
    const selectAgent = () => {
      state.selectedAgentId = lane.dataset.agentId || null;
      render();
      window.setTimeout(() => $("agent-inspector")?.scrollIntoView({ block: "nearest", behavior: "smooth" }), 0);
    };
    lane.addEventListener("click", (event) => {
      if (event.target.closest("details, summary, pre, code, a, button")) return;
      selectAgent();
    });
    lane.addEventListener("keydown", (event) => {
      if (event.key !== "Enter" && event.key !== " ") return;
      event.preventDefault();
      selectAgent();
    });
  });
  $("agent-inspector-close")?.addEventListener("click", () => {
    state.selectedAgentId = null;
    render();
  });
}

$("launch").addEventListener("click", launch);
$("sign-out").addEventListener("click", () => signOut("You have signed out."));
$("scenario").addEventListener("change", renderScenarioDescription);
renderScenarioDescription();

bootstrapAuth();
