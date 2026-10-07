# The Plan in Plain Language

This is the readable version of the [full implementation plan](2026-10-07-implementation-plan.md). Same phases, same order, no code.

## The whole system in one picture

Think of a small office with a front desk.

- **The front desk (channel)** is Telegram. Messages come in here and replies go out here. It does no thinking.
- **The manager (Orchestrator agent)** reads each message and decides who should handle it. If a request has two parts, the manager sends it to two people in order.
- **The clerk (Task agent)** is the only one allowed to write in the task book. The clerk works out exactly which task and which person a message means.
- **The checker (Reviewer agent)** looks at every entry before the clerk writes it. Is this a duplicate? Is the date already past? Is this really what the person asked for? If something is off, the checker sends back a question instead of letting it through.
- **The advisor (Advisor agent)** can read the task book but cannot write in it. The advisor answers "what should I focus on today?" and writes daily plans and summaries.
- **The task book (database)** stores tasks, people, the history of every change, and a log of what each agent did.
- **The alarm clock (scheduler)** rings at set times and asks the advisor to prepare a morning plan, reminders, or a weekly summary. Nobody has to ask.

Two things are deliberately not agents. Deciding which task is most urgent is arithmetic, so plain code does it and the advisor explains the result. Remembering the conversation is storage, so it is a table.

## What happens when someone sends a message

Example: "Create a high priority task for John to deploy the frontend by Friday."

1. Telegram hands the message to the front desk.
2. The manager reads it and says: this is a task change, send it to the clerk.
3. The clerk looks up John, works out which date "Friday" is, and proposes a new task.
4. The checker looks for a similar existing task and checks the date. All fine, so it approves.
5. The clerk writes the task into the task book.
6. The manager writes the reply: "Created: Deploy the frontend, high priority, assigned to John, due Friday 9 October."
7. The front desk sends it back to Telegram.

Every step is logged, so the dashboard can show exactly which agent did what. That log is the evidence that this is a real multi-agent system and not one prompt doing everything.

## The phases

### Day 1 — Foundation (no AI yet)

| Phase | What gets built | Why it comes first | How you know it works |
|---|---|---|---|
| **0. Setup** | The empty project: folders, settings file, a server that starts | Everything else sits on this | You open a web page and see the server's auto-generated API page |
| **1. Data layer** | The task book: tables for people, tasks, change history, conversation, agent log, sent reminders | Agents need somewhere to read and write | Tests create a task and read it back |
| **2. Task logic** | All the rules for tasks: create, update, complete, search, filter, find look-alikes, and the urgency score | This is the one place allowed to touch the task book, so every later part goes through it | Tests such as "an overdue task ranks above a task due next week" |
| **3. API** | Web addresses the dashboard will call to list, search, and change tasks | The dashboard and the demo need them, and this is the FastAPI part of the grade | You can create and list tasks from the browser's API page |

At the end of Day 1 there is a working task manager with no AI in it. That is on purpose: if the AI misbehaves later, we know the base underneath is solid.

**Your part on Day 1:** the Architecture Copilot session, the Telegram bot token, and asking HiDevs for the real deadline.

### Day 2 — The agents

| Phase | What gets built | Why | How you know it works |
|---|---|---|---|
| **4. Agent engine** | The shared machinery every agent uses: talk to Claude, let it ask for a tool, run the tool, give the result back, repeat until it has an answer. Plus the log of every step | Build it once, and each agent becomes a short file: a job description and a list of tools | Tests run the loop with a pretend AI that follows a script, so they are free and repeatable |
| **5. The specialists** | The clerk, the advisor, and the checker, each with its own instructions and tools | These do the real work | Tests such as "when two tasks match, the clerk asks which one and changes nothing" |
| **6. The manager** | The Orchestrator, conversation memory, and a web address for chatting without Telegram | This ties the specialists together | We send the six example sentences from the assignment to the real Claude and read the replies |
| **7. Telegram** | The front desk: connect the bot so real messages reach the manager | First moment the real product exists | **You** chat with the bot on your phone |

**Your part on Day 2:** test in a real Telegram chat and tell me what feels wrong. You are better at this than I am.

### Day 3 — Autonomy, dashboard, delivery

| Phase | What gets built | Why | How you know it works |
|---|---|---|---|
| **8. Proactive help** | The alarm clock and its four jobs: morning plan, due-soon reminders, overdue follow-ups, weekly summary. Each can also be triggered by hand | The "autonomous assistance" part of the grade. The manual trigger exists so the demo video does not have to wait until 9 a.m. | You press a button and a morning plan arrives in Telegram. Press twice and you still get only one reminder |
| **9. Dashboard** | Three screens built to your 14 UI rules: the task list, a task detail panel, and a view of what the agents did (see below) | Required, but only 5% of the grade | A checklist with one line per rule, each checked in a real browser |
| **10. Delivery** | README, architecture document, demo data, Docker setup | The other deliverables | Someone else can run it from the README |

**Your part on Day 3:** your written evaluation of the Copilot's design, and the demo video.

## The dashboard and your UI rules

The dashboard is plain web pages served by the same server, so the whole product starts with one command. The look is clean and restrained: neutral colours, one accent colour, a readable table, nothing decorative.

Three screens:

1. **Tasks**: a row of counts at the top (Open, Overdue, Due today, Completed this week), a search and filter bar, and the task table.
2. **Task detail**: a panel that slides in beside the list, so the list stays visible. It shows every field and the history of changes.
3. **Agent activity**: for each message, which agent did what. This is the screen to show in the demo video.

Several of your 14 rules say the same thing, so I grouped them. This is what each group becomes on screen:

| Your rules | What you will see |
|---|---|
| **Consistency** (1, 14) | Every colour, font size, and spacing value is defined once and reused. A status badge looks the same on every screen |
| **Shortcuts and productivity** (2, 13) | Keyboard keys for the common actions (`/` search, `n` new task, `c` complete, `?` help). One-click filters such as "Overdue". A quick-add box where you type a sentence and the agents fill in the task |
| **Feedback** (3) | Every action shows a short message saying what happened. Loading and saving are visible |
| **Closure** (4) | Each action ends clearly: a confirmation, the panel closes, the row updates |
| **Error handling and prevention** (5, 9) | Problems appear under the field in plain words. Save is disabled until the form is valid. Actions that make no sense are greyed out |
| **Easy reversal** (6) | An Undo button appears for 8 seconds after any change. Tasks are cancelled, never deleted, and can be restored |
| **User in control** (7) | The list never rearranges by itself. New changes show a "Refresh" banner that you click |
| **Low memory load** (8) | Active filters stay visible as chips. Dates show both ways ("Fri 9 Oct, in 2 days") |
| **Satisfaction and speed** (10) | Changes appear instantly. Works on a phone-width screen |
| **Accessibility** (11) | Usable with keyboard only, readable contrast, works with screen readers, light and dark themes |
| **Ease of use** (12) | Three screens, one main button per screen, plain labels |

One limit to know about: there is no login. A "Viewing as" menu picks which team member you are. Real login is out of scope for three days, and the README will say so.

## Four safety rules built into the design

1. **Only one part of the code touches the database.** Agents ask for changes through tools. If a task is wrong, there is one place to look.
2. **The checker cannot be skipped.** The check is in the code path for every write, not a suggestion in a prompt.
3. **The AI never does date maths or scoring.** Code computes; the AI explains. This removes a whole class of confident wrong answers.
4. **Tests never call the real AI.** They use a scripted stand-in, so they cost nothing and give the same result every time.

## Choices I made that you can change

| Choice | Why | Cost of changing later |
|---|---|---|
| Telegram | Free, fastest to set up, runs from your laptop | Low. The front desk is a separate piece |
| Claude | Reliable at choosing and filling in tools | Low. One file talks to the AI |
| SQLite (a database that is a single file) | Nothing to install | Low. One setting switches to a bigger database |
| Four agents, not seven | Each one has a job I can defend. More would be decoration | Medium |
| Dashboard as plain web pages, no separate frontend framework | One command starts everything; the dashboard is 5% of the grade | Medium |

## What is still open

- The real deadline.
- Whether you confirm Telegram.
