#!/usr/bin/env python3
"""
Import KIRA job trajectories into a local Langfuse instance.

Each task becomes one Langfuse Trace.
Each agent step becomes a Span inside that trace.
CF planner overhead is tracked as a separate nested Span per step.

Prerequisites:
  1. Start Langfuse:  cd tools/langfuse && docker-compose up -d
  2. Open http://localhost:3000, create a project, copy the API keys.
  3. Run:
       python scripts/upload_to_langfuse.py <job_dir> \
           --host http://localhost:3000 \
           --pk pk-lf-... \
           --sk sk-lf-... \
           [--tag base]          # or --tag cf  (shown as a trace tag)
           [--limit 5]           # upload only first N tasks (for testing)

Tips:
  - Upload both base and CF jobs with different --tag values.
  - In the Langfuse UI: Traces → filter by tag or session_id to compare.
  - "Cost" column shows per-step USD; the trace header shows totals.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

from langfuse import Langfuse

# ── helpers ───────────────────────────────────────────────────────────────────

SUFFIX_RE = re.compile(r"__[A-Za-z0-9]{6,}$")
_CF_STEP_RE = re.compile(
    r'"changed_plan"\s*:\s*(true|false).*?'
    r'"planner_extra_llm_calls"\s*:\s*(\d+).*?'
    r'"planner_extra_input_tokens"\s*:\s*(\d+).*?'
    r'"planner_extra_output_tokens"\s*:\s*(\d+).*?'
    r'"planner_extra_cost_usd"\s*:\s*([\d.]+)',
    re.DOTALL,
)
_CF_MODE_RE      = re.compile(r'"planner_mode"\s*:\s*"([^"]+)"')
_CF_TRIGGERED_RE = re.compile(r'"planner_triggered"\s*:\s*(true|false)')


def _ts(s: str | None) -> datetime | None:
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None


def _cf_from_message(msg: str) -> dict | None:
    """Extract CF planner stats embedded in an agent step message."""
    m = _CF_STEP_RE.search(msg)
    if not m:
        return None
    mode = (_CF_MODE_RE.search(msg) or type("", (), {"group": lambda self, n: "unknown"})()).group(1)
    triggered = (_CF_TRIGGERED_RE.search(msg) or type("", (), {"group": lambda self, n: "false"})()).group(1) == "true"
    return dict(
        changed_plan=m.group(1) == "true",
        llm_calls=int(m.group(2)),
        input_tokens=int(m.group(3)),
        output_tokens=int(m.group(4)),
        cost_usd=float(m.group(5)),
        planner_mode=mode,
        triggered=triggered,
    )


def _load_rewards(job_dir: Path) -> dict[str, float | None]:
    result_path = job_dir / "result.json"
    rewards: dict[str, float] = {}
    if not result_path.exists():
        return rewards
    data = json.load(open(result_path))
    for ev in (data.get("stats", {}).get("evals", {}) or {}).values():
        for r_str, trials in ((ev.get("reward_stats") or {}).get("reward") or {}).items():
            for t in trials:
                try:
                    rewards[t] = float(r_str)
                except ValueError:
                    pass
    return rewards


# ── uploader ──────────────────────────────────────────────────────────────────

def upload_job(job_dir: Path, lf: Langfuse, tag: str, limit: int | None) -> None:
    rewards = _load_rewards(job_dir)
    job_name = job_dir.name

    task_dirs = [c for c in sorted(job_dir.iterdir())
                 if c.is_dir() and "__" in c.name]
    if limit:
        task_dirs = task_dirs[:limit]

    for i, trial_dir in enumerate(task_dirs, 1):
        trial_name = trial_dir.name
        task_name  = SUFFIX_RE.sub("", trial_name)
        reward     = rewards.get(trial_name)
        passed     = reward is not None and reward >= 1.0

        traj_path = trial_dir / "agent" / "trajectory.json"
        if not traj_path.exists():
            print(f"  [{i}/{len(task_dirs)}] {task_name}  — no trajectory, skipping")
            continue

        try:
            data = json.load(open(traj_path))
        except json.JSONDecodeError:
            print(f"  [{i}/{len(task_dirs)}] {task_name}  — bad JSON, skipping")
            continue

        steps = data.get("steps", [])
        fm    = data.get("final_metrics", {}) or {}

        # find trace timestamps from first/last step
        ts_list = [_ts(s.get("timestamp")) for s in steps if s.get("timestamp")]
        trace_start = min(ts_list) if ts_list else None
        trace_end   = max(ts_list) if ts_list else None

        trace = lf.trace(
            name=task_name,
            session_id=job_name,
            tags=[tag, "pass" if passed else "fail" if reward is not None else "error",
                  job_name],
            metadata={
                "trial_name":   trial_name,
                "job_dir":      job_name,
                "reward":       reward,
                "passed":       passed,
                "total_prompt_tokens":     fm.get("total_prompt_tokens"),
                "total_completion_tokens": fm.get("total_completion_tokens"),
                "total_cost_usd":          fm.get("total_cost_usd"),
            },
            input={"task": task_name},
            output={"reward": reward, "passed": passed},
        )

        agent_steps = [s for s in steps if s.get("source") == "agent"]
        for step in agent_steps:
            sid   = step.get("step_id", "?")
            ts    = _ts(step.get("timestamp"))
            m     = step.get("metrics", {}) or {}
            msg   = step.get("message", "")
            tools = step.get("tool_calls", [])
            obs   = step.get("observation", {})

            # Find the next step timestamp as end time
            next_steps = [s for s in steps if s.get("step_id", 0) > int(sid)]
            end_ts = _ts(next_steps[0]["timestamp"]) if next_steps else ts

            cf = _cf_from_message(msg)

            span = trace.span(
                name=f"step-{sid}",
                start_time=ts,
                end_time=end_ts,
                metadata={
                    "step_id":        sid,
                    "model":          step.get("model_name"),
                    "tool_calls":     [tc.get("function_name") for tc in tools],
                    "cf_mode":        cf["planner_mode"] if cf else None,
                    "cf_triggered":   cf["triggered"]    if cf else False,
                    "cf_changed_plan": cf["changed_plan"] if cf else False,
                },
                input={
                    "tools": [{"fn": tc["function_name"], "args": tc.get("arguments", {})}
                               for tc in tools],
                },
                output={
                    "observation": (obs.get("results") or [{}])[0].get("content", "")[:500],
                },
            )

            # Main LLM generation span
            span.generation(
                name="main-llm",
                model=step.get("model_name", "unknown"),
                usage={
                    "input":  m.get("prompt_tokens", 0),
                    "output": m.get("completion_tokens", 0),
                },
                input=msg[:300] if msg else None,
                metadata={"cost_usd": m.get("cost_usd")},
            )

            # CF planner span (separate, not counted in main)
            if cf and cf["input_tokens"] > 0:
                span.generation(
                    name="cf-planner",
                    model=step.get("model_name", "unknown"),
                    usage={
                        "input":  cf["input_tokens"],
                        "output": cf["output_tokens"],
                    },
                    metadata={
                        "planner_mode":  cf["planner_mode"],
                        "triggered":     cf["triggered"],
                        "changed_plan":  cf["changed_plan"],
                        "cost_usd":      cf["cost_usd"],
                    },
                )

        print(f"  [{i}/{len(task_dirs)}] {task_name:<45} "
              f"reward={str(reward):<5}  steps={len(agent_steps)}")

    lf.flush()
    print(f"\nDone. View at: http://localhost:3000 → Sessions → {job_name}")


def main():
    p = argparse.ArgumentParser(description="Upload KIRA trajectories to local Langfuse.")
    p.add_argument("job_dir", type=Path, help="Job directory (base or CF)")
    p.add_argument("--host", default="http://localhost:3000")
    p.add_argument("--pk",   required=True, help="Langfuse public key  (pk-lf-...)")
    p.add_argument("--sk",   required=True, help="Langfuse secret key  (sk-lf-...)")
    p.add_argument("--tag",  default="run",  help="Tag added to every trace (e.g. 'base' or 'cf')")
    p.add_argument("--limit", type=int, default=None, help="Upload only first N tasks")
    args = p.parse_args()

    if not args.job_dir.is_dir():
        print(f"ERROR: {args.job_dir} is not a directory", file=sys.stderr)
        sys.exit(1)

    lf = Langfuse(
        host=args.host,
        public_key=args.pk,
        secret_key=args.sk,
    )

    print(f"Uploading: {args.job_dir.name}  [tag={args.tag}]")
    upload_job(args.job_dir, lf, tag=args.tag, limit=args.limit)


if __name__ == "__main__":
    main()
