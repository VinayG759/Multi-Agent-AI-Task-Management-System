// The task panel: details and history, plus the form for creating and editing.
// It opens beside the list, so the list stays visible behind it.

import { EVENTS, changeTask, completeTask, reopenTask } from "./actions.js";
import { api } from "./api.js";
import { state, writeUrl } from "./state.js";
import { toast } from "./toast.js";
import { PRIORITY_LABEL, STATUS_LABEL, badge, formatDateTime, formatDue, fromLocalInput, h, isOpen, relativeTime, toLocalInput } from "./util.js";

const TITLE_MAX = 200;
const DESCRIPTION_MAX = 2000;

const dialog = () => document.getElementById("drawer");
const body = () => document.getElementById("drawer-body");

let current = null;      // the task shown, with its history
let mode = "closed";     // closed | view | edit | new
let dirty = false;
let callbacks = { reload: async () => {}, focusRow: () => {} };

export function isDrawerOpen() {
  return mode !== "closed";
}

export function drawerMode() {
  return mode;
}

// ---------- open and close ----------

function show() {
  if (!dialog().open) dialog().showModal();
}

export function closeDrawer({ force = false } = {}) {
  if (mode === "closed") return true;
  if (dirty && !force && !window.confirm("Discard your unsaved changes?")) return false;
  const id = current?.id;
  mode = "closed";
  dirty = false;
  current = null;
  dialog().close();
  writeUrl(null);
  if (id) callbacks.focusRow(id);
  else document.getElementById("new-task").focus();
  return true;
}

export async function openTask(id, { edit = false } = {}) {
  if (dirty && !window.confirm("Discard your unsaved changes?")) return;
  dirty = false;
  try {
    current = await api.getTask(id);
  } catch (error) {
    toast(`Couldn't open task #${id}: ${error.message}`, { type: "error" });
    return;
  }
  state.selectedId = current.id;
  writeUrl(current.id);
  if (edit) renderForm(current);
  else renderView();
  show();
}

export function openNew() {
  if (dirty && !window.confirm("Discard your unsaved changes?")) return;
  current = null;
  renderForm(null);
  show();
}

export function editCurrent() {
  if (mode === "view" && current) renderForm(current);
}

// ---------- detail view ----------

function listTask() {
  return state.tasks.find((t) => t.id === current.id) || current;
}

function describeChange(event) {
  if (event.action === "created") return "Created the task";
  const parts = Object.entries(event.changes || {}).map(([field, change]) => {
    const name = { due_at: "due date", assignee_id: "assignee" }[field] || field;
    const show = (v) => {
      if (v === null || v === "") return "none";
      if (field === "due_at") return formatDateTime(v);
      if (field === "status") return STATUS_LABEL[v] || v;
      if (field === "priority") return PRIORITY_LABEL[v] || v;
      if (field === "assignee_id") return state.users.find((u) => u.id === v)?.name || `person #${v}`;
      return String(v).length > 60 ? `${String(v).slice(0, 60)}…` : String(v);
    };
    return `${name}: ${show(change.from)} → ${show(change.to)}`;
  });
  return parts.join("; ") || event.action;
}

function actorName(actor) {
  if (actor === "user:dashboard") return "Dashboard";
  if (actor === "system:seed") return "Demo data";
  if (actor.startsWith("agent:")) return `${actor.slice(6)[0].toUpperCase()}${actor.slice(7)} agent`;
  return actor;
}

function head(title) {
  return h("div", { class: "drawer-head" },
    h("h2", { id: "drawer-title", tabindex: "-1" }, title),
    h("button", { class: "btn btn-secondary btn-small", type: "button", onclick: () => closeDrawer() }, "Close ", h("kbd", {}, "Esc")));
}

function renderView() {
  mode = "view";
  dirty = false;
  const task = current;
  const open = isOpen(task);
  const due = formatDue(task.due_at, open);

  const facts = h("dl", { class: "detail-list" },
    h("dt", {}, "Assignee"), h("dd", {}, task.assignee ? task.assignee.name : "Unassigned"),
    h("dt", {}, "Due"), h("dd", { class: due?.overdue ? "due-overdue" : "" }, due ? `${due.main}, ${due.sub}` : "No date"),
    h("dt", {}, "Created by"), h("dd", {}, task.creator ? task.creator.name : "Dashboard"),
    h("dt", {}, "Created"), h("dd", {}, formatDateTime(task.created_at)),
    task.completed_at && h("dt", {}, "Completed"), task.completed_at && h("dd", {}, formatDateTime(task.completed_at)),
  );

  const history = h("ul", { class: "history" }, task.events.map((event) =>
    h("li", {},
      h("span", { class: "who" }, actorName(event.actor)), " ",
      h("span", { class: "when", title: formatDateTime(event.created_at) }, relativeTime(event.created_at)),
      h("div", { class: "what" }, describeChange(event)))));

  const act = async (fn) => { await fn(listTask()); };

  body().replaceChildren(
    head(task.title),
    h("div", { class: "drawer-content" },
      h("div", { class: "detail-badges" },
        h("span", { class: "task-id" }, `#${task.id}`),
        badge(task.status, STATUS_LABEL[task.status]),
        badge(task.priority, `${PRIORITY_LABEL[task.priority]} priority`),
        task.overdue && badge("failed", "Overdue")),
      facts,
      h("div", {}, h("h3", {}, "Description"),
        task.description ? h("p", { class: "description" }, task.description) : h("p", { class: "muted" }, "No description.")),
      h("div", {}, h("h3", {}, "History"), history)),
    h("div", { class: "drawer-foot" },
      h("button", { class: "btn btn-secondary", type: "button", onclick: () => renderForm(task) }, "Edit ", h("kbd", {}, "E")),
      task.status === "done"
        ? h("button", { class: "btn btn-secondary", type: "button", onclick: () => act(reopenTask) }, "Reopen")
        : h("button", { class: "btn btn-primary", type: "button", disabled: task.status === "cancelled", onclick: () => act(completeTask) }, "Mark complete"),
      h("span", { class: "spacer" }),
      task.status === "cancelled"
        ? h("button", { class: "btn btn-secondary", type: "button", onclick: () => changeTask(listTask(), { status: "todo" }, `Restored "${task.title}".`) }, "Restore task")
        : h("button", { class: "btn btn-danger", type: "button", onclick: () => changeTask(listTask(), { status: "cancelled" }, `Cancelled "${task.title}". It can be restored.`) }, "Cancel task")),
  );
  body().querySelector("#drawer-title").focus();
}

// ---------- create / edit form ----------

function renderForm(task) {
  mode = task ? "edit" : "new";
  dirty = false;
  let touched = false;
  let pastDateConfirmed = false;

  const title = h("input", { id: "f-title", class: "input", type: "text", value: task?.title || "", required: true, "aria-describedby": "f-title-meta f-title-error" });
  const description = h("textarea", { id: "f-description", class: "input", rows: "5", "aria-describedby": "f-description-meta f-description-error" }, task?.description || "");
  const assignee = h("select", { id: "f-assignee", class: "input" },
    h("option", { value: "" }, "Unassigned"),
    state.users.map((u) => h("option", { value: String(u.id) }, u.name)));
  assignee.value = task ? String(task.assignee?.id ?? "") : String(state.me ?? "");
  const priority = h("select", { id: "f-priority", class: "input" },
    Object.entries(PRIORITY_LABEL).map(([value, label]) => h("option", { value }, label)));
  priority.value = task?.priority || "medium";
  const status = h("select", { id: "f-status", class: "input" },
    Object.entries(STATUS_LABEL).map(([value, label]) => h("option", { value }, label)));
  status.value = task?.status || "todo";
  const due = h("input", { id: "f-due", class: "input", type: "datetime-local", value: toLocalInput(task?.due_at), "aria-describedby": "f-due-warning" });

  const titleError = h("span", { id: "f-title-error", class: "field-error", role: "alert" });
  const titleCount = h("span", {});
  const descriptionError = h("span", { id: "f-description-error", class: "field-error", role: "alert" });
  const descriptionCount = h("span", {});
  const dueWarning = h("span", { id: "f-due-warning", class: "field-warning", role: "alert" });
  const formError = h("div", { class: "form-error", role: "alert", hidden: true });
  const save = h("button", { class: "btn btn-primary", type: "submit", form: "task-form" }, task ? "Save changes" : "Create task");
  const saveHint = h("span", { class: "hint" });

  const values = () => ({
    title: title.value.trim(),
    description: description.value,
    assignee_id: assignee.value ? Number(assignee.value) : null,
    priority: priority.value,
    status: status.value,
    due_at: fromLocalInput(due.value),
  });

  function validate(serverFields = {}) {
    const v = values();
    const errors = { ...serverFields };
    if (!v.title) errors.title ??= "Enter a title, so the task can be found later.";
    else if (v.title.length > TITLE_MAX) errors.title ??= `The title is ${v.title.length - TITLE_MAX} characters too long (maximum ${TITLE_MAX}).`;
    if (v.description.length > DESCRIPTION_MAX) errors.description ??= `The description is ${v.description.length - DESCRIPTION_MAX} characters too long (maximum ${DESCRIPTION_MAX}).`;

    // Errors appear once the person has typed or tried to save, not on an untouched form.
    titleError.textContent = touched ? errors.title || "" : "";
    title.setAttribute("aria-invalid", String(Boolean(touched && errors.title)));
    descriptionError.textContent = errors.description || "";
    description.setAttribute("aria-invalid", String(Boolean(errors.description)));
    titleCount.textContent = `${title.value.trim().length} / ${TITLE_MAX}`;
    descriptionCount.textContent = `${description.value.length} / ${DESCRIPTION_MAX}`;

    const valid = !errors.title && !errors.description;
    save.disabled = !valid;
    saveHint.textContent = valid ? "" : (v.title ? "Fix the highlighted field to save." : "Enter a title to save.");
    return valid;
  }

  function onInput() {
    dirty = true;
    touched = true;
    formError.hidden = true;
    validate();
  }

  due.addEventListener("input", () => {
    pastDateConfirmed = false;
    dueWarning.textContent = "";
    save.textContent = task ? "Save changes" : "Create task";
  });

  async function submit(event) {
    event.preventDefault();
    touched = true;
    if (!validate()) { (title.value.trim() && title.value.trim().length <= TITLE_MAX ? description : title).focus(); return; }

    const v = values();
    const dueChanged = !task || v.due_at !== (task.due_at ? new Date(task.due_at).toISOString() : null);
    if (v.due_at && dueChanged && new Date(v.due_at) < new Date() && !pastDateConfirmed) {
      pastDateConfirmed = true;
      dueWarning.textContent = "This date is in the past, so the task will be overdue straight away. Save again to confirm.";
      save.textContent = "Save with past date";
      due.focus();
      return;
    }

    save.disabled = true;
    save.textContent = "Saving…";
    try {
      if (task) await saveEdit(task, v);
      else await saveNew(v);
    } catch (error) {
      save.textContent = task ? "Save changes" : "Create task";
      touched = true;
      validate(error.fields || {});
      if (!Object.keys(error.fields || {}).length) {
        formError.textContent = `Not saved: ${error.message} Your changes are still here.`;
        formError.hidden = false;
      }
    }
  }

  const form = h("form", { id: "task-form", class: "form", novalidate: true, onsubmit: submit, oninput: onInput },
    formError,
    h("div", { class: "field" },
      h("label", { for: "f-title" }, "Title"), title,
      h("div", { id: "f-title-meta", class: "field-meta" }, titleError, titleCount)),
    h("div", { class: "field" },
      h("label", { for: "f-description" }, "Description (optional)"), description,
      h("div", { id: "f-description-meta", class: "field-meta" }, descriptionError, descriptionCount)),
    h("div", { class: "form-row" },
      h("div", { class: "field" }, h("label", { for: "f-assignee" }, "Assignee"), assignee),
      h("div", { class: "field" }, h("label", { for: "f-priority" }, "Priority"), priority)),
    h("div", { class: "form-row" },
      h("div", { class: "field" }, h("label", { for: "f-due" }, "Due (optional)"), due),
      task && h("div", { class: "field" }, h("label", { for: "f-status" }, "Status"), status)),
    dueWarning,
  );

  body().replaceChildren(
    head(task ? `Edit task #${task.id}` : "New task"),
    h("div", { class: "drawer-content" }, form),
    h("div", { class: "drawer-foot" },
      save,
      h("button", { class: "btn btn-secondary", type: "button", onclick: () => { if (task) { if (!dirty || window.confirm("Discard your unsaved changes?")) renderView(); } else closeDrawer(); } }, "Cancel"),
      saveHint),
  );
  validate();
  title.focus();
}

async function saveNew(v) {
  const created = await api.createTask({
    title: v.title, description: v.description, assignee_id: v.assignee_id, priority: v.priority, due_at: v.due_at,
    creator_id: state.me ? Number(state.me) : null,
  });
  dirty = false;
  await callbacks.reload();
  closeDrawer({ force: true });
  state.selectedId = created.id;
  document.dispatchEvent(new CustomEvent(EVENTS.render));
  callbacks.focusRow(created.id);
  toast(`Created "${created.title}".`, { actionLabel: "Open", onAction: () => openTask(created.id) });
}

async function saveEdit(task, v) {
  const before = {
    title: task.title, description: task.description, assignee_id: task.assignee ? task.assignee.id : null,
    priority: task.priority, status: task.status, due_at: task.due_at ? new Date(task.due_at).toISOString() : null,
  };
  const patch = Object.fromEntries(Object.entries(v).filter(([key, value]) => value !== before[key]));
  if (!Object.keys(patch).length) {
    dirty = false;
    renderView();
    toast("No changes to save.");
    return;
  }
  await changeTask(listTask(), patch, `Saved changes to "${v.title}".`, { throwOnError: true });
  dirty = false;
}

// ---------- setup ----------

export function initDrawer(options) {
  callbacks = options;
  // Esc and clicking outside both go through closeDrawer, so unsaved edits are never lost silently.
  dialog().addEventListener("cancel", (event) => { event.preventDefault(); closeDrawer(); });
  dialog().addEventListener("click", (event) => { if (event.target === dialog()) closeDrawer(); });
  // If the browser closes the dialog on its own, keep our record of it in step.
  dialog().addEventListener("close", () => { mode = "closed"; dirty = false; current = null; });

  // Keep the open panel current after any change, including an Undo.
  document.addEventListener(EVENTS.changed, async (event) => {
    if (mode === "closed" || !current || event.detail?.task?.id !== current.id) return;
    try {
      current = await api.getTask(current.id);
      if (mode === "view" || !dirty) renderView();
    } catch { /* the list still shows the saved state */ }
  });
}
