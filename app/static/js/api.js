// Every call to the backend goes through here, so errors have one shape: ApiError { message, fields, status }.

export class ApiError extends Error {
  constructor(message, { fields = {}, status = 0 } = {}) {
    super(message);
    this.fields = fields;
    this.status = status;
  }
}

const FIELD_NAMES = { title: "Title", description: "Description", due_at: "Due date", priority: "Priority", status: "Status" };

function plainMessage(issue) {
  const field = issue.loc[issue.loc.length - 1];
  const name = FIELD_NAMES[field] || "This field";
  if (issue.type === "string_too_short" || issue.type === "missing") return `${name} is required.`;
  if (issue.type === "string_too_long") return `${name} is too long (maximum ${issue.ctx?.max_length} characters).`;
  return `${name}: ${issue.msg}`;
}

async function request(method, path, body) {
  let response;
  try {
    response = await fetch(path, {
      method,
      headers: body ? { "Content-Type": "application/json" } : undefined,
      body: body ? JSON.stringify(body) : undefined,
    });
  } catch {
    throw new ApiError("Can't reach the server. Check that it is running, then try again.");
  }
  if (response.ok) return response.json();

  let detail;
  try { detail = (await response.json()).detail; } catch { detail = null; }

  if (response.status === 422 && Array.isArray(detail)) {
    const fields = {};
    for (const issue of detail) fields[issue.loc[issue.loc.length - 1]] = plainMessage(issue);
    throw new ApiError("Some fields need fixing.", { fields, status: 422 });
  }
  const message = typeof detail === "string" ? detail : `The server returned an error (${response.status}).`;
  throw new ApiError(message, { status: response.status });
}

export const api = {
  listTasks: (params) => request("GET", `/api/tasks?${params}`),
  getTask: (id) => request("GET", `/api/tasks/${id}`),
  createTask: (data) => request("POST", "/api/tasks", data),
  updateTask: (id, data) => request("PATCH", `/api/tasks/${id}`, data),
  users: () => request("GET", "/api/users"),
  stats: () => request("GET", "/api/stats"),
  status: () => request("GET", "/api/status"),
  chat: (userId, text) => request("POST", "/api/chat", { user_id: userId, text }),
  runs: () => request("GET", "/api/runs?limit=30"),
  run: (id) => request("GET", `/api/runs/${id}`),
  jobs: () => request("GET", "/api/proactive/jobs"),
  runJob: (name) => request("POST", `/api/proactive/${name}/run`),
};
