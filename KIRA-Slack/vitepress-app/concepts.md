# Concepts

This page explains the product concepts that shape KiraClaw. It is meant to answer a simple question first:

**What kind of system is KiraClaw?**

KiraClaw is not just a chat UI with a model behind it. It is a local desktop agent runtime built around one always-on daemon, multiple client surfaces, explicit tool use, and observable local state.

## Core idea

KiraClaw is built around three ideas:

- `agentd` is the long-running local daemon.
- `desktop` and channel adapters are client surfaces attached to that daemon.
- a reusable core runtime provides the core agent loop, and KiraClaw adds product-specific tools and behavior on top.

That means KiraClaw behaves like one agent system with multiple ears and mouths rather than several disconnected bots.

## The daemon boundary

The heart of KiraClaw is `agentd`.

It owns:

- sessions
- scheduler state
- memory runtime
- MCP runtime
- channel runtimes
- background processes

`agentd` is implemented as a local FastAPI server, but conceptually it is more useful to think of it as the product's local daemon and execution boundary.

This matters because the desktop app, Talk surface, and channels all converge on the same runtime.

## Desktop surfaces

The desktop app is the primary local client for `agentd`.

Today it exposes these main product surfaces:

- `Talk`
- `Logs`
- `Diagnostics`
- `Skills`
- `Schedules`
- `Settings`

These surfaces do different jobs:

- `Talk` is the direct local conversation surface.
- `Logs` shows run-oriented agent traces.
- `Diagnostics` shows daemon-oriented resources and structured daemon events.

So the desktop app is both a chat shell and a local control surface.

## Channels as adapters

Slack, Telegram, and Discord are treated as thin adapters on top of the same runtime.

Each adapter:

1. receives an external message
2. normalizes it into an internal `session_id`
3. opens a run through the same session/engine boundary
4. publishes a result back through `speak` or channel delivery

This keeps KiraClaw closer to one runtime with many delivery surfaces than to a collection of separate bots.

## Runs and sessions

The basic execution unit in KiraClaw is a `run`.

A run is how one request gets handled:

1. a request arrives
2. `SessionManager` creates a `RunRecord`
3. the run is queued inside the target session lane
4. conversation and memory context are prepared
5. `KiraClawEngine.run()` executes the agent
6. the result is finalized through `speak`, `submit`, and logs

Sessions are the continuity layer around those runs.

Examples:

- `desktop:local`
- `schedule:<id>`
- Slack channel/thread sessions
- Telegram chat/thread sessions
- Discord channel/thread sessions

Inside one session:

- runs are serialized
- recent run records remain available
- idle lanes are cleaned up later

## Logs vs Diagnostics

KiraClaw intentionally separates two kinds of observability.

### Logs

`Logs` is about **agent runs**.

It shows things like:

- prompt
- streamed text
- tool start/end
- tool result
- spoken reply
- internal summary
- error

This answers: **How did the agent handle this request?**

### Diagnostics

`Diagnostics` is about **daemon state**.

It is backed by:

- `GET /v1/resources`
- `GET /v1/daemon-events`

It shows things like:

- channel state
- memory runtime state
- MCP runtime state
- scheduler state
- process state

This answers: **What is the daemon currently managing?**

That distinction is important. KiraClaw does not treat agent traces and daemon state as the same kind of log.

## Memory

KiraClaw keeps durable memory inside the workspace.

Basic structure:

- `workspace/memories/*.md`
- `workspace/memories/index.json`

Main categories:

- `users`
- `channels`
- `misc`

The agent works with memory through tools such as:

- `memory_search`
- `memory_save`
- `memory_index_search`
- `memory_index_save`

This makes KiraClaw memory closer to an indexed local memory store than to a hidden model-only state.

## Tool model

KiraClaw uses four main tool layers:

### Core base tools

- `bash`
- `read`
- `write`
- `edit`
- `grep`
- `glob`
- `submit`
- `skill`

### KiraClaw native tools

- `speak`
- `memory_*`
- Slack tools
- Telegram tools
- Discord tools
- `exec`
- `process`

### MCP tools

External retrieval and integration tools loaded by MCP.

### skills

Workspace instruction packages under `skills/` with `SKILL.md`.

So KiraClaw is not a pure tool platform. It is an assistant product with a layered tool surface.

## speak and submit

One of KiraClaw's most important design choices is the split between `submit` and `speak`.

- `submit` finishes the run internally and finalizes the run summary.
- `speak` is the explicit act of talking to the outside world.

That separation enables useful behaviors:

- do internal work and end silently
- let a schedule run without always producing a visible message
- keep internal completion separate from outward communication

This is why KiraClaw can behave more like an assistant runtime than a simple "always reply" bot.

## Background work

KiraClaw now has a background process concept built around:

- `BackgroundProcessManager`
- `exec`
- `process`

### exec

`exec` starts shell work that may finish quickly or continue past the current run.

- if it finishes quickly, it returns the result directly
- if not, it returns a `session_id`

### process

`process` manages an existing background session.

Supported actions:

- `list`
- `poll`
- `log`
- `kill`
- `clear`

This means KiraClaw can now keep long-running shell work alive at daemon scope and inspect it later from the same conversation flow.

## Scheduler

KiraClaw's scheduler is the time-based mechanism that opens new runs.

Conceptually, both of these are the same kind of thing:

- a user directly triggering a run
- the scheduler triggering a run at a configured time

Both open a new run inside the daemon.

This makes the scheduler KiraClaw's current time-based wakeup model.

An important detail is that scheduled runs do not have to speak. If nothing is wrong or useful to say, they can quietly submit and finish.

## Safety and approval direction

`bash` and `exec` both pass through shell safety rules.

Current rule types:

- `DENY`
- `ALLOW`
- `ASK`

Today, KiraClaw is still closer to a deny-first safety model than to a full approval model. Dangerous patterns are blocked, safe patterns are allowed, and the complete natural-language approval flow is still a future concept.

The likely direction for KiraClaw is conversation-based approval:

- store pending approval in session state
- ask in natural language
- interpret the next user answer as approve / deny / always allow
- continue the original action after approval

## What KiraClaw is today

KiraClaw is best described as:

- conversation-centric
- local-daemon-based
- channel-aware
- run-oriented

And it is now expanding with:

- background processes
- structured diagnostics
- a more explicit local control-plane foundation

So KiraClaw already has meaningful daemon structure, but its core feel is still that of an assistant product rather than a full control-plane platform.

## OpenClaw-inspired ideas

KiraClaw is also informed by ideas that are more explicit in OpenClaw.

The most relevant ones are:

- gateway-centered runtime thinking
- `exec / process` as first-class shell primitives
- natural-language approvals
- background completion notification
- event-driven wakeup concepts
- workflow-shell ideas such as Lobster

KiraClaw does not implement all of these today, but they are useful reference points for where the product could grow next.
