// Keyboard shortcuts for frequent users. They are ignored while typing in a field.

import { completeTask } from "./actions.js";
import { drawerMode, editCurrent, isDrawerOpen, openNew, openTask } from "./drawer.js";
import { state } from "./state.js";
import { moveSelection, selectedTask } from "./tasks.js";
import { isTyping } from "./util.js";

export function initShortcuts() {
  const help = document.getElementById("help");
  document.getElementById("help-button").addEventListener("click", () => help.showModal());

  document.addEventListener("keydown", (event) => {
    if (event.ctrlKey || event.metaKey || event.altKey || isTyping(event.target) || help.open) return;
    const key = event.key.toLowerCase();

    if (event.key === "?") { event.preventDefault(); help.showModal(); return; }

    if (isDrawerOpen()) {
      if (key === "e" && drawerMode() === "view") { event.preventDefault(); editCurrent(); }
      return;
    }
    if (state.view !== "tasks") return;

    const task = selectedTask();
    if (key === "/") { event.preventDefault(); document.getElementById("search").focus(); }
    else if (key === "n") { event.preventDefault(); openNew(); }
    else if (key === "j") { event.preventDefault(); moveSelection(1); }
    else if (key === "k") { event.preventDefault(); moveSelection(-1); }
    else if (key === "enter" && task && !event.target.closest("button, a, summary")) { event.preventDefault(); openTask(task.id); }
    else if (key === "e" && task) { event.preventDefault(); openTask(task.id, { edit: true }); }
    else if (key === "c" && task && task.status !== "done" && task.status !== "cancelled") { event.preventDefault(); completeTask(task); }
  });
}
