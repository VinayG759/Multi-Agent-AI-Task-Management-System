// What the dashboard currently shows. Filters live in the URL, so a view survives refresh and can be shared.

export const DEFAULT_FILTERS = { q: "", status: "open", priority: "", assignee: "", sort: "due_at", overdue: false, due: "" };
export const PAGE_SIZE = 50;

export const state = {
  view: "tasks",
  filters: { ...DEFAULT_FILTERS },
  limit: PAGE_SIZE,
  tasks: [],
  total: 0,
  selectedId: null,
  users: [],
  me: null,
  system: { assistant: false, telegram: false, scheduler: false },
};

export function readUrl() {
  const params = new URLSearchParams(location.search);
  const f = { ...DEFAULT_FILTERS };
  if (params.has("q")) f.q = params.get("q");
  if (params.has("status")) f.status = params.get("status") === "all" ? "" : params.get("status");
  if (params.has("priority")) f.priority = params.get("priority");
  if (params.has("assignee")) f.assignee = params.get("assignee");
  if (params.has("sort")) f.sort = params.get("sort");
  f.overdue = params.get("overdue") === "1";
  f.due = ["today", "week"].includes(params.get("due")) ? params.get("due") : "";
  state.filters = f;
  state.view = location.hash === "#activity" ? "activity" : "tasks";
  return params.get("task");
}

export function writeUrl(taskId = null) {
  const f = state.filters;
  const params = new URLSearchParams();
  if (f.q) params.set("q", f.q);
  if (f.status !== DEFAULT_FILTERS.status) params.set("status", f.status || "all");
  if (f.priority) params.set("priority", f.priority);
  if (f.assignee) params.set("assignee", f.assignee);
  if (f.sort !== DEFAULT_FILTERS.sort) params.set("sort", f.sort);
  if (f.overdue) params.set("overdue", "1");
  if (f.due) params.set("due", f.due);
  if (taskId) params.set("task", taskId);
  const query = params.toString();
  history.replaceState(null, "", `${location.pathname}${query ? `?${query}` : ""}${location.hash}`);
}

/** The filters as the API expects them. */
export function apiParams() {
  const f = state.filters;
  const params = new URLSearchParams();
  if (f.status === "open") { params.append("status", "todo"); params.append("status", "in_progress"); }
  else if (f.status) params.append("status", f.status);
  if (f.priority) params.set("priority", f.priority);
  if (f.assignee) params.set("assignee_id", f.assignee);
  if (f.q.trim()) params.set("q", f.q.trim());
  if (f.overdue) params.set("overdue", "true");
  if (f.due) {
    const now = new Date();
    const startOfToday = new Date(now.getFullYear(), now.getMonth(), now.getDate());
    const end = new Date(startOfToday);
    end.setDate(end.getDate() + (f.due === "today" ? 1 : 7));
    if (f.due === "today") params.set("due_after", startOfToday.toISOString());
    params.set("due_before", end.toISOString());
  }
  params.set("sort", f.sort);
  params.set("limit", String(state.limit));
  return params;
}

export function loadMe() {
  try { return localStorage.getItem("taskpilot.me"); } catch { return null; }
}

export function saveMe(id) {
  try { localStorage.setItem("taskpilot.me", String(id)); } catch { /* storage unavailable: the choice lasts for this visit only */ }
}

export function userById(id) {
  return state.users.find((u) => String(u.id) === String(id)) || null;
}
