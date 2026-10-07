// Small helpers shared by every screen. Text always goes in through textContent, never innerHTML.

export const STATUS_LABEL = { todo: "To do", in_progress: "In progress", done: "Done", cancelled: "Cancelled" };
export const PRIORITY_LABEL = { urgent: "Urgent", high: "High", medium: "Medium", low: "Low" };
export const OPEN = ["todo", "in_progress"];

/** Build an element: h("button", { class: "btn", onclick: fn, "aria-label": "x" }, "Save") */
export function h(tag, props = {}, ...children) {
  const el = document.createElement(tag);
  for (const [key, value] of Object.entries(props || {})) {
    if (value === null || value === undefined || value === false) continue;
    if (key === "class") el.className = value;
    else if (key === "dataset") Object.assign(el.dataset, value);
    else if (key.startsWith("on")) el.addEventListener(key.slice(2), value);
    else if (key in el && !key.startsWith("aria-") && key !== "list" && key !== "form") el[key] = value;
    else el.setAttribute(key, value === true ? "" : value);
  }
  for (const child of children.flat()) {
    if (child === null || child === undefined || child === false) continue;
    el.append(child instanceof Node ? child : document.createTextNode(String(child)));
  }
  return el;
}

export function badge(kind, label) {
  return h("span", { class: `badge badge-${kind}` }, label);
}

export function isOpen(task) {
  return OPEN.includes(task.status);
}

function dayDiff(date, now) {
  const start = (d) => new Date(d.getFullYear(), d.getMonth(), d.getDate()).getTime();
  return Math.round((start(date) - start(now)) / 86400000);
}

function plural(n, word) {
  return `${n} ${word}${n === 1 ? "" : "s"}`;
}

/** One date format for the whole app: "Fri 9 Oct" plus "in 2 days, 18:00". */
export function formatDue(iso, taskOpen = true) {
  if (!iso) return null;
  const date = new Date(iso);
  const now = new Date();
  const days = dayDiff(date, now);
  const overdue = taskOpen && date < now;
  const time = date.toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit" });
  let when;
  if (overdue) when = days === 0 ? "overdue today" : `overdue by ${plural(-days, "day")}`;
  else if (days === 0) when = "today";
  else if (days === 1) when = "tomorrow";
  else if (days > 1) when = `in ${plural(days, "day")}`;
  else when = `${plural(-days, "day")} ago`;
  return {
    main: date.toLocaleDateString(undefined, { weekday: "short", day: "numeric", month: "short" }),
    sub: `${when}, ${time}`,
    overdue,
  };
}

export function formatDateTime(iso) {
  if (!iso) return "";
  return new Date(iso).toLocaleString(undefined, {
    weekday: "short", day: "numeric", month: "short", hour: "2-digit", minute: "2-digit",
  });
}

export function relativeTime(iso) {
  const seconds = Math.round((Date.now() - new Date(iso).getTime()) / 1000);
  if (seconds < 60) return "just now";
  if (seconds < 3600) return `${plural(Math.floor(seconds / 60), "minute")} ago`;
  if (seconds < 86400) return `${plural(Math.floor(seconds / 3600), "hour")} ago`;
  return `${plural(Math.floor(seconds / 86400), "day")} ago`;
}

/** ISO timestamp -> the value a datetime-local input expects, in the viewer's own timezone. */
export function toLocalInput(iso) {
  if (!iso) return "";
  const d = new Date(iso);
  const pad = (n) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

export function fromLocalInput(value) {
  return value ? new Date(value).toISOString() : null;
}

/** Say something to screen-reader users without moving focus. */
export function announce(text) {
  const live = document.getElementById("live");
  live.textContent = "";
  requestAnimationFrame(() => { live.textContent = text; });
}

export function debounce(fn, ms) {
  let timer;
  return (...args) => {
    clearTimeout(timer);
    timer = setTimeout(() => fn(...args), ms);
  };
}

export function isTyping(target) {
  return target instanceof HTMLElement && (target.isContentEditable || ["INPUT", "TEXTAREA", "SELECT"].includes(target.tagName));
}
