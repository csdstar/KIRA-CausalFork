# OpenClaw Concepts

This document summarizes the main concepts behind OpenClaw from the product's own point of view. It gathers the ideas spread across the official docs into one narrative centered on what OpenClaw treats as first-class.

## One-Line Definition

OpenClaw is **a gateway-centered agent system that runs agents, tools, channels, nodes, and background work on top of an always-on Gateway**.

Three ideas define it:

- the `Gateway` is the central daemon
- clients and nodes both attach to that Gateway
- LLM calls are one subsystem inside the Gateway runtime

## Core Components

### Gateway

The center of OpenClaw is the `Gateway`.

- It is a long-running daemon/service.
- It exposes a typed WebSocket API.
- It also provides HTTP and control APIs.
- It manages channels, nodes, sessions, tools, and background processes.
- It acts as the source of truth for events and logs.

So OpenClaw is not best understood as "a chatbot with tools". It is better understood as **a system where the Gateway operates the full agent runtime**.

### Clients

Clients are control-plane clients attached to the Gateway.

Examples:

- mac app
- CLI
- Web UI
- automation runner

They are not meant to read local runtime state directly. They talk to the Gateway and consume sessions, tools, and events from there.

### Nodes

Nodes are execution surfaces that connect to the Gateway with `role: node`.

They are not just UIs. They are capability providers.

Examples:

- `canvas.*`
- `camera.*`
- `screen.record`
- `location.get`

So in OpenClaw, a node is both **another device** and **a runtime that contributes additional capabilities**.

### WebChat

WebChat is also just another client attached to the same Gateway.

That means OpenClaw is not a product where chat has its own backend first. It is **a product where multiple clients share one Gateway**.

### Agent Runtime

OpenClaw uses an embedded pi runtime for agents.

Important details:

- pi is embedded rather than spawned as a separate subprocess wrapper
- raw pi base tools are not exposed unchanged
- the Gateway reshapes the tool surface

So the agent runtime in OpenClaw is best understood as **an execution core living inside the Gateway**, not as a separate standalone engine.

## Agent Loop

In OpenClaw, the agent loop is not just a model call.

The documented flow is:

- intake
- context assembly
- model inference
- tool execution
- streaming
- persistence

So a loop is not simply "answer one question". It is **the authoritative execution path for a run inside the Gateway**.

### entry points

Representative entry points:

- `agent`
- `agent.wait`
- CLI `agent`

So an agent run is itself a kind of Gateway request.

### important characteristics

#### per-session serialization

OpenClaw serializes runs with a per-session queue.

So concurrent runs do not collide inside the same session.

#### event-stream-first behavior

The loop does not stay hidden internally. It streams outward.

Main stream types:

- `assistant`
- `tool`
- `lifecycle`

So in OpenClaw, a run is not just a final return value. **Its execution state is part of the control plane.**

#### runtime orchestration

Tool calls, retries, compaction, suppression, and message shaping are all handled within the loop.

So the agent loop is not just a model wrapper. It is **the center of runtime orchestration**.

## Sessions

In OpenClaw, a session is not just a conversation ID.

A session is **an official state unit owned by the Gateway**.

Examples:

- a main DM session
- a group or channel session
- a thread/session key

And Gateway settings define policies around sessions:

- `session.dmScope`
- identity links
- pruning
- reset
- maintenance

So a session is not something loosely invented by a transport adapter. It is **part of Gateway policy and lifecycle**.

### why that matters

This ties sessions to:

- transcript management
- context management
- DM isolation
- reset policy
- pruning policy

So in OpenClaw, a session is not only about conversation continuity. It is **a core state unit in the Gateway operating model**.

## Memory

The source of truth for OpenClaw memory is not model internals. It is files in the workspace.

Typical structure:

- `MEMORY.md`
- `memory/YYYY-MM-DD.md`

So memory is not hidden state. It is **a durable workspace artifact managed by the Gateway**.

### important concepts

- `MEMORY.md` holds durable facts
- `memory/YYYY-MM-DD.md` holds daily notes
- memory tools focus on search/get access
- vector or hybrid retrieval can be layered on
- a silent memory flush turn can run before compaction to update durable memory

So memory is not just retrieval. It is **a storage layer connected to session lifecycle**.

## Tool Structure

OpenClaw treats the tool system itself as a major product axis.

The documented stack has three layers:

- `tools`
- `skills`
- `plugins`

### tools

Tools are typed functions the agent directly calls.

Representative built-in tools include:

- `exec`
- `process`
- `browser`
- `web_search`
- `web_fetch`
- `read`
- `write`
- `edit`
- `apply_patch`
- `message`
- `canvas`
- `nodes`
- `cron`
- `gateway`
- `image`
- `image_generate`
- `sessions_*`

So the tool surface spans shell work, filesystem work, web, messaging, automation, and session control.

### skills

Skills are instruction packages that explain how and when to use tools.

So in OpenClaw, a skill is not a replacement for a tool. It is **an instruction layer that improves tool usage**.

### plugins

Plugins are extension packages that can bundle channels, providers, tools, and skills.

So OpenClaw is not just a tool collection. It has the character of **a plugin-capable tool platform**.

## Tool Policy

In OpenClaw, tools are not just callable functions. They are policy subjects.

Important ideas include:

- allow / deny
- tool profiles
- tool groups
- provider-specific tool limits

So whether a tool can be used depends not only on its implementation, but also on **Gateway policy**.

## exec / process

OpenClaw's shell model is centered on `exec / process`, not on `bash`.

### exec

`exec` starts shell work.

- If the command finishes quickly, it returns a foreground result.
- If it does not, it becomes a background session.
- `yieldMs` determines when foreground becomes background.
- `background: true` starts directly in session form.

So `exec` is a shell entry point that spans both synchronous and asynchronous execution.

### process

`process` manages background sessions.

Representative actions:

- `list`
- `poll`
- `log`
- `write`
- `kill`
- `clear`
- `remove`

So OpenClaw handles long-running shell work as **a Gateway runtime concept**.

### what a background session means

A background process session lives in Gateway memory.

That means after an LLM turn ends, the Gateway still holds:

- process state
- output
- exit status

The important distinction is:

- persistence does not belong to the LLM
- persistence belongs to the Gateway runtime

## exec approvals

OpenClaw includes an approval concept for `exec`.

The core flow is:

- a risky or unapproved command is not run immediately
- it becomes an approval request
- the user is asked to approve it
- the system receives `allow once`, `allow always`, or `deny`

So shell safety in OpenClaw is not limited to a denylist. It can extend into **a conversational approval flow**.

That approval may be expressed through UI controls, channel responses, or natural-language replies.

## Background Completion Notification

OpenClaw includes a Gateway-native background completion notification model.

Its main pieces are:

- `tools.exec.notifyOnExit`
- system event enqueue
- a heartbeat request

The flow looks like this:

1. a background exec is running
2. the Gateway detects exit
3. the Gateway creates a system event
4. the Gateway requests a heartbeat
5. the agent/LLM wakes up again to process the event

Two points matter:

- exit detection itself is done by the Gateway, not by the LLM
- heartbeat is what wakes the loop so that completion can be handled

So completion notification is **a runtime feature**, not just an application-level convenience.

## Logs

OpenClaw has two distinct log layers.

### Gateway logs

These are logs from the daemon itself.

- log files
- `openclaw logs --follow`
- the Logs tab in the Control UI

So this layer is for operational debugging and daemon observation.

### process/session logs

These are background process outputs such as stdout/stderr.

They are read through `process poll/log`.

So OpenClaw clearly separates **runtime logs** from **session/process logs**.

## heartbeat

In OpenClaw, heartbeat is closer to an event-driven wakeup mechanism than to a simple timer.

Important point:

- heartbeat does not directly monitor process completion
- the Gateway creates an event, and heartbeat is what reconnects that event to the agent loop

So heartbeat is best understood as **a bridge from Gateway events back into agent execution**.

## doctor

`doctor` is not just a log viewer.

Its character is:

- diagnostics
- repair
- migration
- health checking

Examples:

- checking config and state
- cleaning stale state
- detecting service drift
- finding auth/environment issues
- running legacy migrations

So in OpenClaw, doctor is **an explicit tool for inspecting and repairing Gateway operating state**.

## Lobster

Lobster is OpenClaw's workflow shell concept.

Its job is to provide:

- multi-step deterministic workflow execution
- approval checkpoints
- resume tokens
- one pipeline run that wraps many tool steps

So Lobster is not just another tool. It is closer to **a workflow runtime that lives on top of the Gateway**.

Important point:

- the Gateway remains the core of OpenClaw
- Lobster is a powerful workflow abstraction layered above it

## Current Product Character

OpenClaw can be summarized like this:

- gateway-centered
- tool-platform-oriented
- event-driven
- session-aware and runtime-aware
- friendly to background work and native automation

So while OpenClaw can behave like an assistant, its deeper character is that of **a control-plane product where the Gateway operates the full agent system**.
