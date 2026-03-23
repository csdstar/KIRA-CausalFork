# KiraClaw Concepts

This document summarizes the core concepts that make up KiraClaw from the product's own point of view. It focuses on what KiraClaw is made of and how it behaves, rather than on comparisons or external references.

## One-Line Definition

KiraClaw is **an assistant product that runs agents through a desktop shell and multiple channel surfaces on top of an always-on local daemon**.

Three ideas define it:

- `agentd` is the long-lived local runtime.
- `desktop` and the channel adapters are client surfaces attached to `agentd`.
- Actual agent reasoning runs on top of the core runtime.

## Core Components

### agentd

`agentd` is the center of KiraClaw.

- It is a FastAPI-based long-running local server.
- It owns sessions, the scheduler, memory, MCP, and channel runtimes.
- Both the desktop app and external channels connect to it.
- It is the closest thing KiraClaw currently has to a gateway.

It is better understood as **the local daemon and execution boundary of the product**, not just as an API server.

### desktop

The desktop app is the default client attached to `agentd`.

Its main surfaces are:

- `Talk`
- `Logs`
- `Diagnostics`
- `Skills`
- `Schedules`
- `Settings`

The desktop app is not only a UI. It is also the main local control surface for the daemon.

### channel adapters

KiraClaw currently connects these channels through adapters:

- Slack
- Telegram
- Discord

Each adapter receives an external message, normalizes it into an internal `session_id`, opens a run through `SessionManager`, and sends the result back out through `speak` or channel delivery.

### Core runtime

KiraClaw's agent loop runs on the core runtime.

- It uses core base tools.
- KiraClaw adds native product tools.
- MCP tools and skills are attached on top.

So KiraClaw is not a product that rebuilt its agent runtime from scratch. It is **a product that hosts a reusable core runtime and layers product-specific tools and behaviors on top of it**.

## Execution Flow

The basic execution unit in KiraClaw is a `run`.

At a high level:

1. An input arrives.
2. `SessionManager` creates a `RunRecord`.
3. The run is added to the queue for that `session_id`.
4. Conversation context and memory context are prepared.
5. `KiraClawEngine.run()` executes the core agent.
6. The result is finalized through `speak`, `submit`, and run logs.

In other words, KiraClaw fundamentally **treats one request as one run**.

## Sessions

A session is the basic unit KiraClaw uses to continue a conversation.

Today, sessions are mostly created by adapters and runtime components.

Examples:

- `desktop:local`
- `schedule:<id>`
- Slack channel/thread sessions
- Telegram chat/thread sessions
- Discord channel/thread sessions

Important properties of the current session model:

- Runs are serialized inside the same session.
- Recent run records are kept in memory.
- Idle lanes are cleaned up after a timeout.

The current session model is best understood as **adapter-driven session routing plus a per-session lane queue**.

## Logs and Diagnostics

KiraClaw currently has two separate observation surfaces.

### run logs

The `Logs` menu shows run logs.

They contain the trace for a single agent run, including:

- prompt
- streamed text
- tool start/end
- tool result
- spoken reply
- internal summary
- error

So run logs answer the question: **how did the agent handle this request?**

### daemon diagnostics

The `Diagnostics` menu shows daemon-level state.

Current APIs:

- `/v1/resources`
- `/v1/daemon-events`

This surface shows things like:

- channel state
- memory runtime state
- MCP runtime state
- scheduler state
- process state

So Diagnostics is not about one request. It is about **what the daemon is currently managing**.

### how this differs from daemon logs

The distinction matters:

- `run logs` are agent run records.
- `daemon events` are structured daemon state changes.
- raw daemon stdout/stderr logs do not yet have a dedicated UI surface.

So today's Diagnostics screen is not a console log viewer. It is **a structured view of daemon resources and daemon events**.

## Memory

KiraClaw stores durable memory inside the workspace.

Basic structure:

- `workspace/memories/*.md`
- `workspace/memories/index.json`

Main categories:

- `users`
- `channels`
- `misc`

Agent-facing tools:

- `memory_search`
- `memory_save`
- `memory_index_search`
- `memory_index_save`

`MemoryRuntime` also injects memory context before runs, and saves are handled through an async queue.

So KiraClaw memory is closer to **an indexed memory store** than to a daily log system.

## Tool Structure

KiraClaw's tool surface is split across roughly four layers.

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

External integration and retrieval tools loaded by the MCP runtime are attached as a separate tool surface.

### skills

Packages under `skills/` with a `SKILL.md` file are loaded as workflow instructions.

In short, KiraClaw uses a **core base tools + native tools + MCP + skills** structure.

## speak and submit

One of the most important design choices in KiraClaw is the split between `submit` and `speak`.

- `submit`
  - finishes the run internally
  - finalizes the run summary
- `speak`
  - actually talks to the outside user

That separation enables useful patterns:

- finish a run without saying anything to the outside world
- do internal work and end silently
- let a scheduler wake a run and only speak when something is wrong

So KiraClaw has a deliberate split between **internal completion and external speech**.

## background exec / process

KiraClaw recently added a background process concept.

Its key pieces are:

- `BackgroundProcessManager`
- `exec`
- `process`

### exec

`exec` starts shell work that may take a while.

- If it finishes quickly, it returns the result directly.
- If it does not, it returns a `session_id`.

### process

`process` manages an already-started background session.

Supported actions:

- `list`
- `poll`
- `log`
- `kill`
- `clear`

### what it means today

This allows KiraClaw to keep long-running shell work alive at daemon scope and inspect it later.

Examples:

- long builds
- tests
- server processes
- extended shell jobs

That means a background process is different from an ordinary run. It is **a unit of work that can outlive the run that started it**.

### what is still missing

The current version does not yet have:

- automatic completion notifications
- notify-on-exit
- heartbeat-based wakeup
- persistence across daemon restarts

So today's `exec/process` should be understood as **manual-inspection background processes, version 1**.

## Scheduler

KiraClaw's scheduler is the mechanism that opens new runs on a time basis.

It is currently implemented with APScheduler.

Conceptually, these are the same kind of thing:

- a user directly triggering a run
- the scheduler triggering a run at a configured time

Both end up opening a new run inside the daemon.

So today the scheduler is KiraClaw's **time-based wakeup mechanism**.

Two important traits:

- a scheduled run does not have to use `speak`
- if nothing is wrong, it can quietly `submit` and end

That is why KiraClaw can already handle many automation scenarios without a heartbeat system.

## Command Safety Rules

Both `bash` and `exec` go through shell safety rules.

The current model is:

- `DENY`
  - immediately block dangerous patterns
- `ALLOW`
  - immediately run known-safe prefixes
- `ASK`
  - requires approval

Default deny examples:

- `rm -rf /`
- `dd if=`
- `curl|sh`

Default allow examples:

- `ls`
- `cat`
- `rg`
- `git status`
- `pytest`

Important current behavior:

- KiraClaw defaults to `ask_by_default = false`
- so most commands are effectively allowed unless explicitly denied
- only clearly dangerous patterns are blocked

So the current structure is not approval-centric. It is closer to **a deny-first safety model**.

## Approval

KiraClaw does not yet have a complete natural-language approval flow.

But the direction is already clear:

- do not immediately fail when `ASK` happens
- store a pending approval in session state
- ask for approval in natural language
- interpret the next user answer as `approve / deny / always allow`
- continue the original command after approval

That means the approval model that best fits KiraClaw is likely **conversation-based natural-language approval**, not button-based UI approval.

## Current Product Character

KiraClaw currently feels most like this:

- a conversation-centric assistant
- a local-daemon-based product
- a channel-aware agent
- a run-oriented execution model

And these are the newer directions now present in the system:

- background processes
- daemon diagnostics
- a more explicit control-plane foundation

So KiraClaw is already meaningfully daemon-centered, but its core feel is still closer to **an assistant product that handles conversations well**.

## What KiraClaw Does Not Yet Have

Representative concepts that KiraClaw still does not have:

- natural-language approval flow
- a resume concept
- background completion notification
- heartbeat-based event wakeup
- doctor
- a raw daemon log surface
- a workflow runtime
- nodes

These are potential future extensions, not concepts that are already present in the core today.
