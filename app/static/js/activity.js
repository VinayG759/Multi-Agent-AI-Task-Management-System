// The Agent activity screen: which agent did what for each request, and the proactive jobs.

import { api } from "./api.js";
import { state, userById } from "./state.js";
import { toast } from "./toast.js";
import { badge, formatDateTime, h, relativeTime } from "./util.js";

const $ = (id) => document.getElementById(id);

const JOB_TITLES = { daily_plan: "Daily plan", due_soon: "Due-soon reminders", overdue_followup: "Overdue follow-up", weekly_summary: "Weekly summary" };
const KIND_LABEL = { llm: "thinking", tool: "tool result", handoff: "hand-off", review: "review", error: "error" };

function stepRow(step) {
  const hasPayload = step.payload && Object.keys(step.payload).length > 0;
  const verdict = step.kind === "review" ? step.payload.decision : null;
  return h("li", { class: "step" },
    h("span", { class: "step-seq" }, step.seq),
    h("span", { class: `step-agent agent-${step.agent}` }, step.agent, h("span", { class: "step-kind" }, KIND_LABEL[step.kind] || step.kind)),
    h("div", { class: "step-summary" },
      verdict && badge(verdict, verdict), verdict && " ",
      step.summary || "(no text)",
      hasPayload && h("details", {}, h("summary", {}, "Show details"), h("pre", {}, JSON.stringify(step.payload, null, 2)))),
  );
}

function runCard(run) {
  const detail = h("div", { class: "run-detail" }, h("p", { class: "hint" }, "Loading steps…"));
  let loaded = false;
  const who = run.user_id ? userById(run.user_id)?.name || `person #${run.user_id}` : "system";

  const card = h("details", { class: "run", ontoggle: async () => {
    if (!card.open || loaded) return;
    try {
      const full = await api.run(run.id);
      loaded = true;
      detail.replaceChildren(
        h("h3", { class: "run-output" }, "Reply"),
        h("div", { class: `reply${run.status === "failed" ? " reply-failed" : ""}` }, full.output || "(empty)"),
        h("h3", { class: "run-output" }, `Steps (${full.steps.length})`),
        h("ol", { class: "steps" }, full.steps.map(stepRow)),
      );
    } catch (error) {
      detail.replaceChildren(h("p", { class: "field-error" }, `Couldn't load this run: ${error.message}`));
    }
  } },
    h("summary", {},
      h("span", { class: "run-input" }, run.trigger === "scheduler" ? `Scheduled: ${JOB_TITLES[run.input] || run.input}` : run.input),
      badge(run.trigger, run.trigger === "chat" ? "Chat" : "Scheduler"),
      badge(run.status, run.status === "completed" ? "Completed" : "Failed"),
      h("span", { class: "run-meta", title: formatDateTime(run.started_at) }, `${who}, ${relativeTime(run.started_at)}`)),
    detail);
  return card;
}

function jobCard(job) {
  const button = h("button", { class: "btn btn-secondary btn-small", type: "button" }, "Run now");
  button.addEventListener("click", async () => {
    button.disabled = true;
    button.textContent = "Running…";
    try {
      const result = await api.runJob(job.name);
      toast(result.sent
        ? `${JOB_TITLES[job.name]}: sent ${result.sent} message${result.sent === 1 ? "" : "s"}.`
        : `${JOB_TITLES[job.name]}: nothing to send right now. Messages go to people who have started the Telegram bot, once per period.`);
      await loadActivity();
    } catch (error) {
      toast(`${JOB_TITLES[job.name]} failed: ${error.message}`, { type: "error" });
    } finally {
      button.disabled = false;
      button.textContent = "Run now";
    }
  });
  return h("div", { class: "job" },
    h("h3", {}, JOB_TITLES[job.name] || job.name),
    h("span", { class: "schedule" }, job.schedule + (job.next_run ? `. Next: ${formatDateTime(job.next_run)}` : state.system.scheduler ? "" : ". Scheduler is off")),
    h("p", {}, job.description),
    h("div", {}, button));
}

export async function loadActivity() {
  try {
    const [jobs, runs] = await Promise.all([api.jobs(), api.runs()]);
    $("jobs").replaceChildren(...jobs.map(jobCard));
    $("runs").replaceChildren(...runs.map(runCard));
    $("runs-empty").hidden = runs.length > 0;
    if (!runs.length) {
      $("runs-empty").replaceChildren(h("strong", {}, "No runs yet"), "Send the assistant a request from the Tasks screen or from Telegram, and it will appear here.");
    }
  } catch (error) {
    $("runs").replaceChildren(h("div", { class: "banner banner-error", role: "alert" }, `Couldn't load agent activity. ${error.message}`));
  }
}

export function initActivity() {
  $("reload-activity").addEventListener("click", loadActivity);
}
