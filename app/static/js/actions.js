// Changing a task, the same way from every screen: show it at once, save it, offer Undo, roll back on failure.

import { api } from "./api.js";
import { state, userById } from "./state.js";
import { toast } from "./toast.js";

export const EVENTS = { render: "tasks:render", changed: "tasks:changed" };

function emit(name, detail) {
  document.dispatchEvent(new CustomEvent(name, { detail }));
}

/** The current value of each field in `patch`, in the shape the API accepts. Used for Undo and rollback. */
function snapshot(task, patch) {
  const previous = {};
  for (const key of Object.keys(patch)) {
    previous[key] = key === "assignee_id" ? (task.assignee ? task.assignee.id : null) : task[key];
  }
  return previous;
}

function applyLocally(task, patch) {
  for (const [key, value] of Object.entries(patch)) {
    if (key === "assignee_id") task.assignee = value === null ? null : userById(value);
    else task[key] = value;
  }
  if ("status" in patch || "due_at" in patch) {
    const open = task.status === "todo" || task.status === "in_progress";
    task.overdue = Boolean(open && task.due_at && new Date(task.due_at) < new Date());
  }
}

function replaceInList(updated) {
  const index = state.tasks.findIndex((t) => t.id === updated.id);
  if (index !== -1) state.tasks[index] = updated;
}

/**
 * Apply `patch` to a task.
 * @param {object} task     the task object currently on screen
 * @param {object} patch    fields to change, e.g. { status: "done" }
 * @param {string} message  what to tell the user when it succeeds
 * @param {boolean} undoable  false when this call is itself an Undo
 * @param {boolean} throwOnError  true when the caller shows the error itself
 */
export async function changeTask(task, patch, message, { undoable = true, throwOnError = false } = {}) {
  const previous = snapshot(task, patch);
  applyLocally(task, patch);
  emit(EVENTS.render);

  try {
    const saved = await api.updateTask(task.id, patch);
    replaceInList(saved);
    emit(EVENTS.changed, { task: saved });
    toast(message, undoable
      ? { actionLabel: "Undo", onAction: () => changeTask(saved, previous, `Undone. "${saved.title}" is back as it was.`, { undoable: false }) }
      : {});
    return saved;
  } catch (error) {
    applyLocally(task, previous);
    emit(EVENTS.render);
    if (throwOnError) throw error;  // a form wants to show the problem next to the field
    toast(`Not saved: ${error.message}`, {
      type: "error",
      actionLabel: "Retry",
      onAction: () => changeTask(task, patch, message, { undoable }),
    });
    return null;
  }
}

export function completeTask(task) {
  return changeTask(task, { status: "done" }, `Marked "${task.title}" complete.`);
}

export function reopenTask(task) {
  return changeTask(task, { status: "todo" }, `Reopened "${task.title}".`);
}

export function setStatus(task, status) {
  const label = { todo: "To do", in_progress: "In progress", done: "Done", cancelled: "Cancelled" }[status];
  return changeTask(task, { status }, `"${task.title}" is now ${label}.`);
}
