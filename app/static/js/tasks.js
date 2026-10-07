// The Tasks screen: counts, filters, and the task table.

import { EVENTS, completeTask, reopenTask, setStatus } from "./actions.js";
import { api } from "./api.js";
import { DEFAULT_FILTERS, PAGE_SIZE, apiParams, state, userById, writeUrl } from "./state.js";
import { PRIORITY_LABEL, STATUS_LABEL, announce, badge, debounce, formatDue, h, isOpen } from "./util.js";

const $ = (id) => document.getElementById(id);
const POLL_MS = 30000;
const SEARCH_DELAY_MS = 250;

let handlers = { openTask: () => {}, openNew: () => {} };
let stats = null;
let baseline = "";
let loading = false;

function signature(list) {
  return `${list.total}|${list.items.map((t) => `${t.id}:${t.updated_at}`).join(",")}`;
}

// ---------- loading ----------

export async function loadTasks({ quiet = false } = {}) {
  if (!quiet) { loading = true; renderRows(); }
  try {
    const [list, counts] = await Promise.all([api.listTasks(apiParams()), api.stats()]);
    state.tasks = list.items;
    state.total = list.total;
    stats = counts;
    baseline = signature(list);
    $("list-error").hidden = true;
    $("refresh-banner").hidden = true;
  } catch (error) {
    showListError(error.message);
  } finally {
    loading = false;
    renderTasks();
  }
}

function showListError(message) {
  const box = $("list-error");
  box.replaceChildren(
    h("span", {}, `Couldn't load tasks. ${message}`),
    h("button", { class: "btn btn-secondary btn-small", type: "button", onclick: () => loadTasks() }, "Try again"),
  );
  box.hidden = false;
}

/** After one of our own changes: note the new server state, so the "tasks changed" banner is not a false alarm. */
async function rebaseline() {
  try {
    const [list, counts] = await Promise.all([api.listTasks(apiParams()), api.stats()]);
    baseline = signature(list);
    stats = counts;
    renderStats();
  } catch { /* the next poll will try again */ }
}

/** The list never rearranges by itself. If something changed elsewhere, offer a refresh. */
async function poll() {
  if (state.view !== "tasks" || document.hidden || loading || !baseline) return;
  try {
    const list = await api.listTasks(apiParams());
    if (signature(list) !== baseline) $("refresh-banner").hidden = false;
  } catch { /* offline: say nothing until the user acts */ }
}

// ---------- filters ----------

function setFilters(patch) {
  Object.assign(state.filters, patch);
  state.limit = PAGE_SIZE;
  state.selectedId = null;
  writeUrl();
  syncControls();
  loadTasks();
}

function syncControls() {
  const f = state.filters;
  if ($("search").value !== f.q) $("search").value = f.q;
  $("filter-status").value = f.status;
  $("filter-priority").value = f.priority;
  $("filter-assignee").value = f.assignee;
  $("sort").value = f.sort;
}

function presetActive(name) {
  const f = state.filters;
  if (name === "mine") return Boolean(state.me) && f.assignee === String(state.me);
  if (name === "overdue") return f.overdue;
  if (name === "week") return f.due === "week";
  if (name === "high") return f.priority === "high";
  return false;
}

function togglePreset(name) {
  const on = !presetActive(name);
  if (name === "mine") setFilters({ assignee: on && state.me ? String(state.me) : "" });
  if (name === "overdue") setFilters({ overdue: on, status: on ? "open" : state.filters.status });
  if (name === "week") setFilters({ due: on ? "week" : "", status: on ? "open" : state.filters.status });
  if (name === "high") setFilters({ priority: on ? "high" : "" });
}

function activeFilterChips() {
  const f = state.filters;
  const chips = [];
  const add = (label, clear) => chips.push(
    h("button", { class: "chip chip-remove", type: "button", "aria-label": `Remove filter: ${label}`, onclick: () => setFilters(clear) }, label, h("span", { "aria-hidden": "true" }, "×")),
  );
  if (f.q) add(`Search: "${f.q}"`, { q: "" });
  if (f.status !== DEFAULT_FILTERS.status) add(`Status: ${f.status ? STATUS_LABEL[f.status] : "All"}`, { status: DEFAULT_FILTERS.status });
  if (f.priority) add(`Priority: ${PRIORITY_LABEL[f.priority]}`, { priority: "" });
  if (f.assignee) add(`Assignee: ${userById(f.assignee)?.name || "Unknown"}`, { assignee: "" });
  if (f.overdue) add("Overdue", { overdue: false });
  if (f.due) add(f.due === "today" ? "Due today" : "Due this week", { due: "" });
  if (chips.length) chips.push(h("button", { class: "btn-link", type: "button", onclick: () => setFilters({ ...DEFAULT_FILTERS, sort: f.sort }) }, "Clear all"));
  return chips;
}

// ---------- rendering ----------

function renderStats() {
  const f = state.filters;
  const s = stats || { open: "–", overdue: "–", due_today: "–", completed_recent: "–" };
  const plain = !f.overdue && !f.due && !f.priority && !f.assignee && !f.q;
  const tiles = [
    { label: "Open", value: s.open, pressed: plain && f.status === "open", go: { ...DEFAULT_FILTERS, sort: f.sort } },
    { label: "Overdue", value: s.overdue, alert: s.overdue > 0, pressed: f.overdue, go: { ...DEFAULT_FILTERS, sort: f.sort, overdue: true } },
    { label: "Due today", value: s.due_today, pressed: f.due === "today", go: { ...DEFAULT_FILTERS, sort: f.sort, due: "today" } },
    { label: "Completed, last 7 days", value: s.completed_recent, pressed: plain && f.status === "done", go: { ...DEFAULT_FILTERS, sort: f.sort, status: "done" } },
  ];
  $("stats").replaceChildren(...tiles.map((t) =>
    h("button", { class: `stat${t.alert ? " stat-alert" : ""}`, type: "button", "aria-pressed": String(Boolean(t.pressed)), onclick: () => setFilters(t.go) },
      h("span", { class: "stat-value" }, t.value),
      h("span", { class: "stat-label" }, t.label)),
  ));
}

function dueCell(task) {
  const due = formatDue(task.due_at, isOpen(task));
  if (!due) return h("span", { class: "muted" }, "No date");
  return h("span", { class: due.overdue ? "due-overdue" : "" }, due.main, h("span", { class: "due-sub" }, due.sub));
}

function row(task) {
  const done = task.status === "done";
  const check = h("button", {
    class: "check", type: "button",
    "aria-pressed": String(done),
    "aria-label": done ? `Reopen "${task.title}"` : `Mark "${task.title}" complete`,
    title: done ? "Reopen" : "Mark complete",
    disabled: task.status === "cancelled",
    onclick: () => (done ? reopenTask(task) : completeTask(task)),
  }, "✓");

  const statusSelect = h("select", {
    class: "input select-inline", "aria-label": `Status of "${task.title}"`,
    onchange: (event) => setStatus(task, event.target.value),
  }, Object.entries(STATUS_LABEL).map(([value, label]) => h("option", { value, selected: task.status === value }, label)));

  return h("tr", {
    dataset: { id: task.id },
    class: isOpen(task) ? "" : "is-closed",
    "aria-selected": String(state.selectedId === task.id),
    onclick: (event) => {
      if (event.target.closest("button, select, a")) return;
      state.selectedId = task.id;
      handlers.openTask(task.id);
    },
  },
    h("td", { class: "col-check" }, check),
    h("td", {}, h("span", { class: "task-id" }, `#${task.id}`),
      h("button", { class: "task-title-button", type: "button", onclick: () => { state.selectedId = task.id; handlers.openTask(task.id); } }, task.title)),
    h("td", {}, task.assignee ? task.assignee.name : h("span", { class: "muted" }, "Unassigned")),
    h("td", {}, badge(task.priority, PRIORITY_LABEL[task.priority])),
    h("td", {}, statusSelect),
    h("td", {}, dueCell(task)),
  );
}

function skeletonRows() {
  return Array.from({ length: 6 }, () =>
    h("tr", { class: "skeleton", "aria-hidden": "true" }, Array.from({ length: 6 }, () => h("td", {}, h("span", {})))));
}

function renderRows() {
  const body = $("task-rows");
  const empty = $("empty");
  if (loading && !state.tasks.length) {
    body.replaceChildren(...skeletonRows());
    empty.hidden = true;
    return;
  }
  body.replaceChildren(...state.tasks.map(row));

  const filtered = JSON.stringify({ ...state.filters, sort: "" }) !== JSON.stringify({ ...DEFAULT_FILTERS, sort: "" });
  empty.hidden = state.tasks.length > 0;
  if (!state.tasks.length) {
    empty.replaceChildren(...(filtered
      ? [h("strong", {}, "No tasks match these filters"), "Try removing a filter or searching for something else.",
         h("div", {}, h("button", { class: "btn btn-secondary", type: "button", onclick: () => setFilters({ ...DEFAULT_FILTERS }) }, "Clear filters"))]
      : [h("strong", {}, "No open tasks"), "Create one here, or type a request to the assistant above.",
         h("div", {}, h("button", { class: "btn btn-primary", type: "button", onclick: () => handlers.openNew() }, "New task"))]));
  }
}

export function renderTasks() {
  renderStats();
  renderRows();

  const shown = state.tasks.length;
  const overdue = state.tasks.filter((t) => t.overdue).length;
  let count = "";
  if (!loading || shown) {
    count = state.total > shown ? `Showing ${shown} of ${state.total} tasks` : `${shown} task${shown === 1 ? "" : "s"}`;
    if (overdue) count += `, ${overdue} overdue`;
  }
  $("result-count").textContent = loading && !shown ? "Loading tasks…" : count;
  $("active-filters").replaceChildren(...activeFilterChips());
  $("more").hidden = state.total <= shown;
  for (const chip of document.querySelectorAll("[data-preset]")) {
    chip.setAttribute("aria-pressed", String(presetActive(chip.dataset.preset)));
  }
}

export function fillAssigneeFilter() {
  $("filter-assignee").replaceChildren(
    h("option", { value: "" }, "Anyone"),
    ...state.users.map((u) => h("option", { value: String(u.id) }, u.name)),
  );
  $("filter-assignee").value = state.filters.assignee;
}

// ---------- keyboard selection ----------

export function selectedTask() {
  return state.tasks.find((t) => t.id === state.selectedId) || null;
}

export function moveSelection(delta) {
  if (!state.tasks.length) return;
  const index = state.tasks.findIndex((t) => t.id === state.selectedId);
  const next = Math.min(state.tasks.length - 1, Math.max(0, index === -1 ? 0 : index + delta));
  state.selectedId = state.tasks[next].id;
  for (const tr of $("task-rows").children) tr.setAttribute("aria-selected", String(Number(tr.dataset.id) === state.selectedId));
  const tr = $("task-rows").querySelector(`[data-id="${state.selectedId}"]`);
  tr?.scrollIntoView({ block: "nearest" });
  announce(`Selected: ${state.tasks[next].title}`);
}

export function focusRow(id) {
  $("task-rows").querySelector(`[data-id="${id}"] .task-title-button`)?.focus();
}

// ---------- setup ----------

export function initTasks(callbacks) {
  handlers = callbacks;
  syncControls();

  $("search").addEventListener("input", debounce((event) => setFilters({ q: event.target.value }), SEARCH_DELAY_MS));
  $("filter-status").addEventListener("change", (e) => setFilters({ status: e.target.value }));
  $("filter-priority").addEventListener("change", (e) => setFilters({ priority: e.target.value }));
  $("filter-assignee").addEventListener("change", (e) => setFilters({ assignee: e.target.value }));
  $("sort").addEventListener("change", (e) => setFilters({ sort: e.target.value }));
  for (const chip of document.querySelectorAll("[data-preset]")) {
    chip.addEventListener("click", () => togglePreset(chip.dataset.preset));
  }
  $("new-task").addEventListener("click", () => handlers.openNew());
  $("refresh").addEventListener("click", () => loadTasks());
  $("more").addEventListener("click", () => { state.limit += PAGE_SIZE; loadTasks({ quiet: true }); });

  document.addEventListener(EVENTS.render, renderTasks);
  document.addEventListener(EVENTS.changed, rebaseline);
  setInterval(poll, POLL_MS);
}
