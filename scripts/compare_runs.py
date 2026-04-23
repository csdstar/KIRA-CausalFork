#!/usr/bin/env python3
"""
Compare two harbor job directories (typically base vs. CF).

Computes:
  - Per-task reward flips (base→CF and CF→base)
  - McNemar's exact test on the flip counts
  - Main-task token totals (from trajectory.json.final_metrics)
  - CF-module token totals (from trial.log `[KIRA CF STATS]` lines)
  - CF overhead ratio (CF tokens / main tokens)
  - Optional aggregate CF planner statistics from *_cf.json sidecars

Usage:
  python scripts/compare_runs.py <base_job_dir> <cf_job_dir> [--verbose]
  python scripts/compare_runs.py <base_job_dir> <cf_job_dir> --json out.json
"""
from __future__ import annotations

import argparse
import json
import math
import re
import sys
from dataclasses import dataclass, field, asdict
from pathlib import Path


# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------

@dataclass
class TrialData:
    task_name: str
    trial_name: str
    trial_dir: Path
    reward: float | None = None          # None means errored/unfinished
    exception: str | None = None

    # Main-task tokens (LLM backbone)
    main_input_tokens: int = 0
    main_output_tokens: int = 0
    main_cached_tokens: int = 0
    main_cost_usd: float = 0.0

    # CF module tokens (only populated in CF jobs)
    cf_llm_calls: int = 0
    cf_input_tokens: int = 0
    cf_output_tokens: int = 0
    cf_cost_usd: float = 0.0
    cf_episodes_triggered: int = 0
    cf_changed_plans: int = 0

    # Aggregated per-episode planner stats (from *_cf.json)
    cf_mode_counts: dict[str, int] = field(default_factory=dict)


@dataclass
class JobSummary:
    job_dir: Path
    trials: dict[str, TrialData]          # keyed by task_name

    @property
    def n_trials(self) -> int:
        return len(self.trials)

    def total_main_tokens(self) -> int:
        return sum(t.main_input_tokens + t.main_output_tokens for t in self.trials.values())

    def total_cf_tokens(self) -> int:
        return sum(t.cf_input_tokens + t.cf_output_tokens for t in self.trials.values())

    def total_main_cost(self) -> float:
        return sum(t.main_cost_usd for t in self.trials.values())

    def total_cf_cost(self) -> float:
        return sum(t.cf_cost_usd for t in self.trials.values())

    def n_passed(self) -> int:
        return sum(1 for t in self.trials.values() if t.reward is not None and t.reward >= 1.0)

    def n_failed(self) -> int:
        return sum(
            1 for t in self.trials.values()
            if t.reward is not None and t.reward < 1.0
        )

    def n_errored(self) -> int:
        return sum(1 for t in self.trials.values() if t.reward is None)


# ---------------------------------------------------------------------------
# Parsers
# ---------------------------------------------------------------------------

TRIAL_SUFFIX_RE = re.compile(r"__[A-Za-z0-9]{6,}$")
CF_STATS_RE = re.compile(
    r"\[KIRA CF STATS\]"
    r"\s+episodes_triggered=(\d+)"
    r"\s+changed_plans=(\d+)"
    r"\s+cf_llm_calls=(\d+)"
    r"\s+cf_input_tokens=(\d+)"
    r"\s+cf_output_tokens=(\d+)"
    r"\s+cf_cost_usd=([\d.]+)"
)


def strip_trial_suffix(trial_name: str) -> str:
    return TRIAL_SUFFIX_RE.sub("", trial_name)


def parse_reward_stats(result: dict) -> tuple[dict[str, float], dict[str, str]]:
    """Return (trial_name -> reward, trial_name -> exception_type)."""
    rewards: dict[str, float] = {}
    exceptions: dict[str, str] = {}
    evals = result.get("stats", {}).get("evals", {}) or {}
    for eval_data in evals.values():
        for reward_str, trials in ((eval_data.get("reward_stats") or {}).get("reward") or {}).items():
            try:
                r = float(reward_str)
            except ValueError:
                continue
            for t in trials:
                rewards[t] = r
        for exc_name, trials in (eval_data.get("exception_stats") or {}).items():
            for t in trials:
                exceptions[t] = exc_name
    return rewards, exceptions


def parse_trajectory_tokens(trajectory_path: Path) -> tuple[int, int, int, float]:
    """Return (input_tokens, output_tokens, cached_tokens, cost_usd)."""
    try:
        with trajectory_path.open() as f:
            data = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return 0, 0, 0, 0.0
    fm = data.get("final_metrics") or {}
    return (
        int(fm.get("total_prompt_tokens", 0) or 0),
        int(fm.get("total_completion_tokens", 0) or 0),
        int(fm.get("total_cached_tokens", 0) or 0),
        float(fm.get("total_cost_usd", 0.0) or 0.0),
    )


def parse_cf_stats_from_log(trial_log: Path) -> dict | None:
    """Find the last `[KIRA CF STATS]` line in trial.log."""
    if not trial_log.exists():
        return None
    last = None
    try:
        with trial_log.open(errors="replace") as f:
            for line in f:
                m = CF_STATS_RE.search(line)
                if m:
                    last = m
    except OSError:
        return None
    if not last:
        return None
    return {
        "episodes_triggered": int(last.group(1)),
        "changed_plans": int(last.group(2)),
        "llm_calls": int(last.group(3)),
        "input_tokens": int(last.group(4)),
        "output_tokens": int(last.group(5)),
        "cost_usd": float(last.group(6)),
    }


def aggregate_cf_sidecars(trial_dir: Path) -> dict[str, int]:
    """Count planner_mode frequencies across all per-episode *_cf.json files."""
    counts: dict[str, int] = {}
    agent_dir = trial_dir / "agent"
    if not agent_dir.exists():
        return counts
    for ep_dir in agent_dir.iterdir():
        if not ep_dir.is_dir():
            continue
        for cf_file in ep_dir.glob("*_cf.json"):
            try:
                with cf_file.open() as f:
                    d = json.load(f)
            except (OSError, json.JSONDecodeError):
                continue
            mode = (d.get("stats") or {}).get("planner_mode") or d.get("mode") or "unknown"
            counts[mode] = counts.get(mode, 0) + 1
    return counts


def load_job(job_dir: Path) -> JobSummary:
    """Walk a job directory and build a JobSummary."""
    result_path = job_dir / "result.json"
    rewards, exceptions = {}, {}
    if result_path.exists():
        with result_path.open() as f:
            rewards, exceptions = parse_reward_stats(json.load(f))

    trials: dict[str, TrialData] = {}
    for child in sorted(job_dir.iterdir()):
        if not child.is_dir() or "__" not in child.name:
            continue
        trial_name = child.name
        task_name = strip_trial_suffix(trial_name)

        td = TrialData(task_name=task_name, trial_name=trial_name, trial_dir=child)

        if trial_name in rewards:
            td.reward = rewards[trial_name]
        if trial_name in exceptions:
            td.exception = exceptions[trial_name]

        (
            td.main_input_tokens,
            td.main_output_tokens,
            td.main_cached_tokens,
            td.main_cost_usd,
        ) = parse_trajectory_tokens(child / "agent" / "trajectory.json")

        cf = parse_cf_stats_from_log(child / "trial.log")
        if cf:
            td.cf_llm_calls = cf["llm_calls"]
            td.cf_input_tokens = cf["input_tokens"]
            td.cf_output_tokens = cf["output_tokens"]
            td.cf_cost_usd = cf["cost_usd"]
            td.cf_episodes_triggered = cf["episodes_triggered"]
            td.cf_changed_plans = cf["changed_plans"]

        td.cf_mode_counts = aggregate_cf_sidecars(child)

        # If the same task appears in multiple trials, keep the most recent one
        # (harbor re-runs produce unique trial suffixes but same task_name).
        trials[task_name] = td

    return JobSummary(job_dir=job_dir, trials=trials)


# ---------------------------------------------------------------------------
# Statistics
# ---------------------------------------------------------------------------

def mcnemar_exact_p(b: int, c: int) -> float:
    """
    Two-sided exact binomial McNemar test.
    b = base-pass → cf-fail count (flips_down)
    c = base-fail → cf-pass count (flips_up)
    H0: b and c are equally likely, H1: not equal.
    Uses sum of probabilities ≤ observed under Binomial(b+c, 0.5).
    Returns 1.0 when b+c == 0 (no discordant pairs).
    """
    n = b + c
    if n == 0:
        return 1.0
    k = min(b, c)
    # P(X ≤ k | X ~ Binom(n, 0.5)) + P(X ≥ n-k | ...)
    total = 0.0
    # Two-tail: sum all outcomes at least as extreme as min(b, c).
    for i in range(n + 1):
        if i <= k or i >= n - k:
            total += math.comb(n, i)
    return min(1.0, total / (2.0 ** n))


def paired_flip_table(base: JobSummary, cf: JobSummary) -> dict:
    """Build the 2x2 reward flip table for tasks present in both jobs."""
    common = set(base.trials) & set(cf.trials)
    pp = pf = fp = ff = 0  # base_pass/fail vs cf_pass/fail
    flips_up: list[str] = []    # base fail → cf pass
    flips_down: list[str] = []  # base pass → cf fail
    for task in sorted(common):
        bt = base.trials[task]
        ct = cf.trials[task]
        if bt.reward is None or ct.reward is None:
            continue  # errored trials excluded from McNemar
        bp = bt.reward >= 1.0
        cp = ct.reward >= 1.0
        if bp and cp:
            pp += 1
        elif bp and not cp:
            pf += 1
            flips_down.append(task)
        elif (not bp) and cp:
            fp += 1
            flips_up.append(task)
        else:
            ff += 1
    p_value = mcnemar_exact_p(pf, fp)
    return {
        "n_common": len(common),
        "n_compared": pp + pf + fp + ff,
        "base_pass_cf_pass": pp,
        "base_pass_cf_fail": pf,
        "base_fail_cf_pass": fp,
        "base_fail_cf_fail": ff,
        "flips_up": flips_up,          # CF rescued these (favorable)
        "flips_down": flips_down,      # CF broke these (unfavorable)
        "mcnemar_p_value": p_value,
        "net_flip": fp - pf,
    }


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------

def build_report(base: JobSummary, cf: JobSummary) -> dict:
    table = paired_flip_table(base, cf)

    base_main_tokens = base.total_main_tokens()
    cf_main_tokens = cf.total_main_tokens()
    cf_extra_tokens = cf.total_cf_tokens()

    # Aggregate CF mode distribution across all CF trials
    mode_totals: dict[str, int] = {}
    trials_with_cf = 0
    total_changed = 0
    total_triggered = 0
    for t in cf.trials.values():
        if t.cf_episodes_triggered or t.cf_changed_plans or t.cf_mode_counts:
            trials_with_cf += 1
        total_changed += t.cf_changed_plans
        total_triggered += t.cf_episodes_triggered
        for mode, n in t.cf_mode_counts.items():
            mode_totals[mode] = mode_totals.get(mode, 0) + n

    cf_overhead_ratio = (
        cf_extra_tokens / cf_main_tokens if cf_main_tokens > 0 else None
    )

    return {
        "base": {
            "job_dir": str(base.job_dir),
            "n_trials": base.n_trials,
            "n_passed": base.n_passed(),
            "n_failed": base.n_failed(),
            "n_errored": base.n_errored(),
            "pass_rate": base.n_passed() / max(base.n_trials, 1),
            "total_main_tokens": base_main_tokens,
            "total_main_cost_usd": base.total_main_cost(),
        },
        "cf": {
            "job_dir": str(cf.job_dir),
            "n_trials": cf.n_trials,
            "n_passed": cf.n_passed(),
            "n_failed": cf.n_failed(),
            "n_errored": cf.n_errored(),
            "pass_rate": cf.n_passed() / max(cf.n_trials, 1),
            "total_main_tokens": cf_main_tokens,
            "total_main_cost_usd": cf.total_main_cost(),
            "total_cf_tokens": cf_extra_tokens,
            "total_cf_cost_usd": cf.total_cf_cost(),
            "cf_token_overhead_ratio": cf_overhead_ratio,
            "cf_trials_with_activity": trials_with_cf,
            "cf_total_episodes_triggered": total_triggered,
            "cf_total_changed_plans": total_changed,
            "cf_mode_distribution": mode_totals,
        },
        "paired": table,
    }


def fmt_pct(x: float) -> str:
    return f"{x * 100:.1f}%" if x is not None else "n/a"


def print_report(report: dict, verbose: bool = False) -> None:
    b, c, p = report["base"], report["cf"], report["paired"]

    print("==== Job Summary ====")
    print(f"BASE: {b['job_dir']}")
    print(f"  trials={b['n_trials']}  pass={b['n_passed']}  fail={b['n_failed']}  "
          f"errored={b['n_errored']}  pass_rate={fmt_pct(b['pass_rate'])}")
    print(f"  main tokens: {b['total_main_tokens']:,}  cost: ${b['total_main_cost_usd']:.4f}")
    print()
    print(f"CF:   {c['job_dir']}")
    print(f"  trials={c['n_trials']}  pass={c['n_passed']}  fail={c['n_failed']}  "
          f"errored={c['n_errored']}  pass_rate={fmt_pct(c['pass_rate'])}")
    print(f"  main tokens: {c['total_main_tokens']:,}  cost: ${c['total_main_cost_usd']:.4f}")
    print(f"  CF   tokens: {c['total_cf_tokens']:,}  cost: ${c['total_cf_cost_usd']:.4f}")
    print(f"  CF overhead ratio (cf/main): {fmt_pct(c['cf_token_overhead_ratio'])}")
    print(f"  CF trials with activity: {c['cf_trials_with_activity']}/{c['n_trials']}")
    print(f"  CF episodes triggered: {c['cf_total_episodes_triggered']}  "
          f"changed_plans: {c['cf_total_changed_plans']}")
    if c["cf_mode_distribution"]:
        modes = ", ".join(f"{k}={v}" for k, v in sorted(c["cf_mode_distribution"].items()))
        print(f"  CF planner_mode histogram: {modes}")

    print()
    print("==== Paired (common tasks) ====")
    print(f"n_common={p['n_common']}  n_compared={p['n_compared']}  "
          f"(errored tasks excluded from McNemar)")
    print(f"             CF pass  CF fail")
    print(f"  base pass    {p['base_pass_cf_pass']:4d}     {p['base_pass_cf_fail']:4d}")
    print(f"  base fail    {p['base_fail_cf_pass']:4d}     {p['base_fail_cf_fail']:4d}")
    print(f"flips_up (CF rescued):   {len(p['flips_up'])}")
    print(f"flips_down (CF broke):   {len(p['flips_down'])}")
    print(f"net flip (up - down):    {p['net_flip']:+d}")
    print(f"McNemar exact two-sided p-value: {p['mcnemar_p_value']:.4f}")

    sig = "SIGNIFICANT (p<0.05)" if p["mcnemar_p_value"] < 0.05 else "not significant"
    print(f"→ {sig}")

    if verbose or len(p["flips_up"]) + len(p["flips_down"]) <= 20:
        print()
        print("---- flips_up (CF rescued) ----")
        for t in p["flips_up"]:
            print(f"  + {t}")
        print("---- flips_down (CF broke) ----")
        for t in p["flips_down"]:
            print(f"  - {t}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Compare two harbor jobs (base vs CF).")
    parser.add_argument("base_job_dir", type=Path)
    parser.add_argument("cf_job_dir", type=Path)
    parser.add_argument("--verbose", "-v", action="store_true",
                        help="Always list all flip tasks")
    parser.add_argument("--json", type=Path,
                        help="Also write full report as JSON to this path")
    args = parser.parse_args()

    for p in (args.base_job_dir, args.cf_job_dir):
        if not p.is_dir():
            print(f"ERROR: {p} is not a directory", file=sys.stderr)
            return 2

    base = load_job(args.base_job_dir)
    cf = load_job(args.cf_job_dir)

    report = build_report(base, cf)
    print_report(report, verbose=args.verbose)

    if args.json:
        with args.json.open("w") as f:
            json.dump(report, f, indent=2, default=str)
        print(f"\nFull report written to {args.json}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
