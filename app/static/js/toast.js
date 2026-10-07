// Feedback for every action. A toast can carry one action button: Undo after a change, Retry after a failure.

import { announce, h } from "./util.js";

const DURATION = 8000;

/** An open dialog sits above the page and blocks clicks outside it, so toasts move inside it while it is open. */
function place(region) {
  const host = document.querySelector("dialog[open]") || document.body;
  if (region.parentElement !== host) host.append(region);
}

document.addEventListener("close", () => place(document.getElementById("toasts")), true);

export function toast(message, { type = "info", actionLabel = null, onAction = null, duration = DURATION } = {}) {
  const region = document.getElementById("toasts");
  let timer = null;

  const close = () => {
    clearTimeout(timer);
    el.remove();
  };
  const startTimer = () => { timer = setTimeout(close, duration); };
  const stopTimer = () => clearTimeout(timer);

  const el = h(
    "div",
    { class: `toast${type === "error" ? " toast-error" : ""}`, onmouseenter: stopTimer, onmouseleave: startTimer, onfocusin: stopTimer, onfocusout: startTimer },
    h("span", { class: "toast-message" }, message),
    actionLabel && h("button", { type: "button", onclick: () => { close(); onAction(); } }, actionLabel),
    h("button", { type: "button", class: "toast-close", "aria-label": "Dismiss", onclick: close }, "×"),
  );

  // Keep the stack short so toasts never cover the page.
  while (region.children.length >= 3) region.firstElementChild.remove();
  place(region);
  region.append(el);
  announce(actionLabel ? `${message} ${actionLabel} is available.` : message);
  startTimer();
  return close;
}
