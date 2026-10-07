// The assistant box: type a request in plain words and the agent team handles it.

import { api } from "./api.js";
import { state } from "./state.js";
import { toast } from "./toast.js";
import { h } from "./util.js";

const $ = (id) => document.getElementById(id);

export function syncQuickAdd() {
  const ready = state.system.assistant && Boolean(state.me);
  $("quick-add-input").disabled = !ready;
  $("quick-add-send").disabled = !ready;
  $("quick-add-hint").textContent = !state.system.assistant
    ? "The assistant is not connected yet: add ANTHROPIC_API_KEY to the .env file and restart the server. Tasks can still be managed by hand below."
    : !state.me
      ? "Choose who you are under \"Viewing as\" to use the assistant."
      : "The same agents that answer in Telegram handle this. Every change is checked by the Reviewer agent first.";
}

export function initQuickAdd({ reload, showRun }) {
  const input = $("quick-add-input");
  const send = $("quick-add-send");
  const reply = $("quick-add-reply");

  $("quick-add").addEventListener("submit", async (event) => {
    event.preventDefault();
    const text = input.value.trim();
    if (!text) { input.focus(); return; }

    send.disabled = true;
    send.textContent = "Working…";
    reply.hidden = false;
    reply.className = "reply";
    reply.replaceChildren("The agents are working on it…");
    try {
      const result = await api.chat(Number(state.me), text);
      reply.className = `reply${result.status === "failed" ? " reply-failed" : ""}`;
      reply.replaceChildren(
        result.reply,
        h("div", { class: "reply-actions" },
          h("button", { class: "btn-link", type: "button", onclick: () => showRun() }, "See which agents handled this"),
          h("button", { class: "btn-link", type: "button", onclick: () => { reply.hidden = true; input.focus(); } }, "Dismiss")));
      if (result.status === "completed") {
        input.value = "";
        await reload();
      }
    } catch (error) {
      reply.hidden = true;
      toast(`The assistant couldn't be reached: ${error.message}`, { type: "error" });
    } finally {
      send.textContent = "Send";
      syncQuickAdd();
      input.focus();
    }
  });
}
