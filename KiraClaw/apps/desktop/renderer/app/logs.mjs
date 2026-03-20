import { byId, escapeHtml, setText } from "./dom.mjs";
import { getDateLocale, t } from "./i18n.mjs";

function formatTime(value) {
  const text = String(value || "").trim();
  if (!text) {
    return "";
  }

  try {
    return new Date(text).toLocaleString(getDateLocale());
  } catch {
    return text;
  }
}

function renderMultiline(value) {
  const text = String(value || "").trim();
  if (!text) {
    return `<span class="log-empty">${escapeHtml(t("common.none"))}</span>`;
  }
  return escapeHtml(text).replace(/\n/g, "<br>");
}

function formatToolPayload(value) {
  if (value === null || value === undefined || value === "") {
    return String(t("common.none"));
  }
  if (typeof value === "string") {
    return value;
  }
  try {
    return JSON.stringify(value, null, 2);
  } catch {
    return String(value);
  }
}

function formatClockTime(value) {
  const text = String(value || "").trim();
  if (!text) {
    return "--:--:--";
  }

  try {
    return new Date(text).toLocaleTimeString(getDateLocale(), {
      hour: "2-digit",
      minute: "2-digit",
      second: "2-digit",
    });
  } catch {
    return text;
  }
}

function buildTraceEntries(row) {
  const entries = [
    {
      kind: "prompt",
      at: row.created_at,
      symbol: "$",
      label: t("logs.prompt"),
      message: row.prompt,
    },
  ];
  const traceEvents = Array.isArray(row.trace_events) ? row.trace_events : [];
  const fallbackToolEvents = Array.isArray(row.tool_events) ? row.tool_events : [];
  let sawSummary = false;
  let sawError = false;
  let sawToolTrace = false;

  for (const event of traceEvents) {
    const type = String(event?.type || "").trim().toLowerCase();
    if (type === "stream") {
      entries.push({
        kind: "stream",
        at: event.at,
        symbol: "~",
        label: t("logs.streamedText"),
        message: event.text,
      });
      continue;
    }
    if (type === "tool_start") {
      sawToolTrace = true;
      entries.push({
        kind: "tool",
        at: event.at,
        symbol: "#",
        label: `${t("logs.toolStart")} ${String(event.name || "").trim()}`.trim(),
        payload: formatToolPayload(event.args),
      });
      continue;
    }
    if (type === "tool_end") {
      sawToolTrace = true;
      entries.push({
        kind: "tool",
        at: event.at,
        symbol: "#",
        label: `${t("logs.toolEnd")} ${String(event.name || "").trim()}`.trim(),
        payload: formatToolPayload(event.result),
      });
      continue;
    }
    if (type === "submit") {
      sawSummary = true;
      entries.push({
        kind: "summary",
        at: event.at,
        symbol: ">",
        label: t("logs.internalSummary"),
        message: event.text,
      });
      continue;
    }
    if (type === "error") {
      sawError = true;
      entries.push({
        kind: "error",
        at: event.at,
        symbol: "!",
        label: t("logs.error"),
        message: event.error,
      });
    }
  }

  if (!sawToolTrace && fallbackToolEvents.length > 0) {
    for (const event of fallbackToolEvents) {
      const phase = String(event?.phase || "").trim().toLowerCase();
      const isStart = phase === "start";
      const isEnd = phase === "end";
      if (!isStart && !isEnd) {
        continue;
      }
      entries.push({
        kind: "tool",
        at: isStart ? (row.started_at || row.created_at) : (row.finished_at || row.started_at || row.created_at),
        symbol: "#",
        label: `${isStart ? t("logs.toolStart") : t("logs.toolEnd")} ${String(event?.name || "").trim()}`.trim(),
        payload: formatToolPayload(isStart ? event?.args : event?.result),
      });
    }
  }

  if (!sawSummary && String(row.internal_summary || "").trim()) {
    entries.push({
      kind: "summary",
      at: row.finished_at,
      symbol: ">",
      label: t("logs.internalSummary"),
      message: row.internal_summary,
    });
  }

  for (const spoken of Array.isArray(row.spoken_messages) ? row.spoken_messages : []) {
    entries.push({
      kind: "spoken",
      at: row.finished_at,
      symbol: "↳",
      label: t("logs.spokenReply"),
      message: spoken,
    });
  }

  if (!sawError && String(row.error || "").trim()) {
    entries.push({
      kind: "error",
      at: row.finished_at,
      symbol: "!",
      label: t("logs.error"),
      message: row.error,
    });
  }

  if (String(row.silent_reason || "").trim()) {
    entries.push({
      kind: "silent",
      at: row.finished_at,
      symbol: "-",
      label: t("logs.silentReason"),
      message: row.silent_reason,
    });
  }

  return entries;
}

function renderTrace(entries) {
  if (!entries.length) {
    return `<span class="log-empty">${escapeHtml(t("common.none"))}</span>`;
  }

  return entries.map((entry) => {
    const payloadBlock = entry.payload
      ? `<pre class="run-trace-payload">${escapeHtml(entry.payload)}</pre>`
      : "";
    const messageBlock = entry.message
      ? `<div class="run-trace-message">${renderMultiline(entry.message)}</div>`
      : "";
    return `
      <div class="run-trace-entry ${escapeHtml(entry.kind || "neutral")}">
        <div class="run-trace-head">
          <span class="run-trace-time">${escapeHtml(formatClockTime(entry.at))}</span>
          <span class="run-trace-symbol">${escapeHtml(entry.symbol || ">")}</span>
          <span class="run-trace-label">${escapeHtml(entry.label || "")}</span>
        </div>
        ${messageBlock}
        ${payloadBlock}
      </div>
    `;
  }).join("");
}

function logCard(row) {
  const displayTime =
    row.state === "running" || row.state === "queued"
      ? (row.started_at || row.created_at)
      : (row.finished_at || row.created_at);
  const stateClass =
    row.state === "completed"
      ? "online"
      : (row.state === "failed" ? "offline" : (row.state === "running" ? "running" : ""));
  const metaParts = [
    row.source || t("logs.unknownSource"),
    row.session_id || "",
    formatTime(displayTime),
  ].filter(Boolean);

  return `
    <article class="simple-item run-log-card terminal-card">
      <div class="run-log-terminal-bar">
        <div class="run-log-terminal-title">${escapeHtml(row.run_id || t("logs.runLabel"))}</div>
        <span class="status-chip ${stateClass}">${escapeHtml(row.state || t("logs.unknownState"))}</span>
      </div>
      <p class="run-log-meta">${escapeHtml(metaParts.join(" · "))}</p>
      <details class="details-card run-log-details" data-run-id="${escapeHtml(row.run_id || "")}">
        <summary>${escapeHtml(t("common.viewDetails"))}</summary>
        <div class="details-body run-log-body run-log-trace">
          ${renderTrace(buildTraceEntries(row))}
        </div>
      </details>
    </article>
  `;
}

export function renderRunLogsState(state) {
  const list = byId("run-log-list");
  if (!list) {
    return;
  }

  const previouslyOpenRunIds = new Set(
    Array.from(list.querySelectorAll(".run-log-details[open][data-run-id]"))
      .map((element) => element.dataset.runId || "")
      .filter(Boolean),
  );

  if (state.runLogError) {
    list.innerHTML = `
      <article class="simple-item">
        <strong>${escapeHtml(t("logs.loadFailedTitle"))}</strong>
        <p>${escapeHtml(state.runLogError)}</p>
      </article>
    `;
    setText(byId("run-log-status"), t("logs.loadFailed", { message: state.runLogError }));
    return;
  }

  if (!Array.isArray(state.runLogs) || state.runLogs.length === 0) {
    list.innerHTML = `
      <article class="simple-item">
        <strong>${escapeHtml(t("logs.noLogsTitle"))}</strong>
        <p>${escapeHtml(t("logs.noLogsBody"))}</p>
      </article>
    `;
    setText(
      byId("run-log-status"),
      state.runLogFile ? t("logs.noRecentLogsWithFile", { path: state.runLogFile }) : t("logs.noRecentLogs"),
    );
    return;
  }

  list.innerHTML = state.runLogs.map(logCard).join("");
  for (const details of list.querySelectorAll(".run-log-details[data-run-id]")) {
    if (previouslyOpenRunIds.has(details.dataset.runId || "")) {
      details.open = true;
    }
  }
  const suffix = state.runLogFile ? ` · ${state.runLogFile}` : "";
  setText(
    byId("run-log-status"),
    t("logs.recentCount", {
      count: state.runLogs.length,
      suffix: state.runLogs.length === 1 ? "" : "s",
      fileSuffix: suffix,
    }),
  );
}

export function bindRunLogActions({ state, onReload, onOpenPath }) {
  byId("reload-run-logs")?.addEventListener("click", onReload);
  byId("open-run-log-file")?.addEventListener("click", () => {
    onOpenPath(state.runLogFile);
  });
}
