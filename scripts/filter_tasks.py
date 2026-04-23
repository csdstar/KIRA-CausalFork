#!/usr/bin/env python3
"""
Filter tasks from a completed harbor job directory by reward status.

Harbor CLI accepts --include-task-name / --exclude-task-name (one flag per name
repeated), so this script also supports emitting flag form for xargs piping.

Status groups (based on result.json reward_stats):
  - passed    reward >= 1.0
  - failed    reward == 0.0
  - partial   0.0 < reward < 1.0   (task with partial credit, if verifier supports it)
  - errored   trial ended in an exception (exception_stats)
  - unfinished trials not present in reward_stats nor exception_stats

Usage examples:
  # Get failing task names, one per line:
  python scripts/filter_tasks.py jobs/base-... --status failed

  # Combine failed + errored for a retry run:
  python scripts/filter_tasks.py jobs/base-... --status failed errored

  # Emit harbor --include-task-name flags for one-shot piping:
  python scripts/filter_tasks.py jobs/base-... --status failed --format harbor-include \
      | xargs harbor run ...

  # Emit a single comma-joined list for manual CLI use:
  python scripts/filter_tasks.py jobs/base-... --status failed --format csv
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path


TRIAL_SUFFIX_RE = re.compile(r"__[A-Za-z0-9]{6,}$")


def strip_trial_suffix(trial_name: str) -> str:
    """trial_name is of the form '<task_name>__<random_id>'; strip the suffix."""
    return TRIAL_SUFFIX_RE.sub("", trial_name)


def classify_trials(result: dict) -> dict[str, set[str]]:
    """Return sets of task_name (not trial_name) grouped by status."""
    groups: dict[str, set[str]] = {
        "passed": set(),
        "failed": set(),
        "partial": set(),
        "errored": set(),
    }

    evals = result.get("stats", {}).get("evals", {}) or {}
    for _eval_name, eval_data in evals.items():
        reward_stats = (eval_data.get("reward_stats") or {}).get("reward") or {}
        for reward_str, trial_names in reward_stats.items():
            try:
                reward = float(reward_str)
            except ValueError:
                continue
            if reward >= 1.0:
                bucket = "passed"
            elif reward <= 0.0:
                bucket = "failed"
            else:
                bucket = "partial"
            for trial in trial_names:
                groups[bucket].add(strip_trial_suffix(trial))

        exc_stats = eval_data.get("exception_stats") or {}
        for _exc_name, trial_names in exc_stats.items():
            for trial in trial_names:
                groups["errored"].add(strip_trial_suffix(trial))

    return groups


def discover_unfinished(job_dir: Path, known: set[str]) -> set[str]:
    """Trial directories that exist on disk but aren't in result.json's reward_stats."""
    unfinished: set[str] = set()
    for child in job_dir.iterdir():
        if not child.is_dir():
            continue
        name = strip_trial_suffix(child.name)
        if name == child.name:
            continue
        if name not in known:
            unfinished.add(name)
    return unfinished


def format_output(names: list[str], fmt: str) -> str:
    if fmt == "lines":
        return "\n".join(names)
    if fmt == "csv":
        return ",".join(names)
    if fmt == "harbor-include":
        return " ".join(f"--include-task-name {n}" for n in names)
    if fmt == "harbor-exclude":
        return " ".join(f"--exclude-task-name {n}" for n in names)
    raise ValueError(f"Unknown format: {fmt}")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Filter tasks from a completed harbor job by reward status.",
    )
    parser.add_argument("job_dir", type=Path, help="Path to the job directory")
    parser.add_argument(
        "--status",
        nargs="+",
        choices=["passed", "failed", "partial", "errored", "unfinished", "all"],
        default=["failed"],
        help="Which statuses to emit (can combine). Default: failed.",
    )
    parser.add_argument(
        "--format",
        choices=["lines", "csv", "harbor-include", "harbor-exclude"],
        default="lines",
        help="Output format. Default: lines (one name per line).",
    )
    parser.add_argument(
        "--stats",
        action="store_true",
        help="Print a summary table to stderr alongside normal output.",
    )
    args = parser.parse_args()

    result_path = args.job_dir / "result.json"
    if not result_path.exists():
        print(f"ERROR: no result.json at {result_path}", file=sys.stderr)
        return 2

    with result_path.open() as f:
        result = json.load(f)

    groups = classify_trials(result)
    known = set().union(*groups.values())
    groups["unfinished"] = discover_unfinished(args.job_dir, known)

    if args.stats:
        print("=== status summary ===", file=sys.stderr)
        for k, v in groups.items():
            print(f"  {k:10s} {len(v):4d}", file=sys.stderr)
        print("======================", file=sys.stderr)

    if "all" in args.status:
        selected: set[str] = set().union(*groups.values())
    else:
        selected = set()
        for s in args.status:
            selected |= groups[s]

    names = sorted(selected)
    if not names:
        print("WARNING: no tasks matched", file=sys.stderr)
        return 1

    print(format_output(names, args.format))
    return 0


if __name__ == "__main__":
    sys.exit(main())
