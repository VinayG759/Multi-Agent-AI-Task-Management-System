// Starts the dashboard and connects the screens.

import { initActivity, loadActivity } from "./activity.js";
import { api } from "./api.js";
import { closeDrawer, initDrawer, isDrawerOpen, openNew, openTask } from "./drawer.js";
import { initQuickAdd, syncQuickAdd } from "./quickadd.js";
import { initShortcuts } from "./shortcuts.js";
import { loadMe, readUrl, saveMe, state, userById } from "./state.js";
import { fillAssigneeFilter, focusRow, initTasks, loadTasks, renderTasks } from "./tasks.js";
import { toast } from "./toast.js";
import { h } from "./util.js";

const $ = (id) => document.getElementById(id);

function showView(view) {
  state.view = view;
  $("view-tasks").hidden = view !== "tasks";
  $("view-activity").hidden = view !== "activity";
  for (const link of document.querySelectorAll(".nav-link")) {
    if (link.dataset.view === view) link.setAttribute("aria-current", "page");
    else link.removeAttribute("aria-current");
  }
  document.title = view === "activity" ? "Agent activity · TaskPilot" : "Tasks · TaskPilot";
  if (view === "activity") loadActivity();
}

function renderSystemStatus() {
  const line = (label, on, offText) =>
    h("li", {}, h("span", {}, label), h("span", { class: on ? "on" : "off" }, on ? "On" : offText));
  $("system-status").replaceChildren(
    line("Assistant", state.system.assistant, "No API key"),
    line("Telegram", state.system.telegram, "Not connected"),
    line("Scheduler", state.system.scheduler, "Off"),
  );
  // Sign out is offered only when the dashboard is password-protected.
  $("sign-out").hidden = !state.system.protected;
}

function fillMe() {
  const select = $("me");
  select.replaceChildren(
    h("option", { value: "" }, state.users.length ? "Choose a person" : "No team members yet"),
    ...state.users.map((u) => h("option", { value: String(u.id) }, u.name)),
  );
  const saved = loadMe();
  state.me = saved && userById(saved) ? saved : (state.users[0] ? String(state.users[0].id) : null);
  select.value = state.me || "";
}

async function loadPeopleAndStatus() {
  try {
    const [users, system] = await Promise.all([api.users(), api.status()]);
    state.users = users;
    state.system = system;
  } catch (error) {
    toast(`Couldn't load the team list: ${error.message}`, { type: "error", actionLabel: "Retry", onAction: start });
  }
  fillMe();
  fillAssigneeFilter();
  renderSystemStatus();
  syncQuickAdd();
}

async function reloadEverything() {
  // The assistant may have added a new team member, so the people lists are refreshed too.
  await loadPeopleAndStatus();
  await loadTasks({ quiet: true });
}

// Filters come from the URL before anything is drawn, so the controls and the list always agree.
const taskFromUrl = readUrl();

async function start() {
  await loadPeopleAndStatus();
  showView(state.view);
  await loadTasks();
  if (taskFromUrl) openTask(Number(taskFromUrl));
}

initTasks({ openTask, openNew });
initDrawer({ reload: () => loadTasks({ quiet: true }), focusRow });
initActivity();
initShortcuts();
initQuickAdd({ reload: reloadEverything, showRun: () => { location.hash = "#activity"; } });

$("me").addEventListener("change", (event) => {
  state.me = event.target.value || null;
  if (state.me) saveMe(state.me);
  syncQuickAdd();
  renderTasks();
});

for (const link of document.querySelectorAll(".nav-link")) {
  link.addEventListener("click", (event) => {
    if (isDrawerOpen() && !closeDrawer()) event.preventDefault();
  });
}

window.addEventListener("hashchange", () => {
  showView(location.hash === "#activity" ? "activity" : "tasks");
  $("main").focus();
});

start();
