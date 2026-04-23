"""
Adaptive counterfactual planner for Terminus-KIRA.

Merges v1 (completion guardrails, reasoning controls, api_key, UsageInfo) with
v2 (adaptive skip/light/full modes, cheap prescreener, PlannerStats for token
isolation, modular ablation flags).

CF token costs are tracked entirely within PlannerStats and are NEVER written
to the main Chat counters — see terminus_kira_cf.py for the accounting boundary.
"""
import json
import re
import time
from dataclasses import dataclass, asdict
from typing import Any, Dict, List, Optional

import litellm
from harbor.agents.terminus_2.terminus_2 import Command

from terminus_kira.reasoning_controls import (
    apply_reasoning_temperature_rules,
    build_reasoning_request_overrides,
)


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

@dataclass
class CFPlannerConfig:
    # Planning mode: "off" | "adaptive" | "always_light" | "always_full"
    mode: str = "adaptive"

    # Candidate budget per mode
    max_candidates_light: int = 2
    max_candidates_full: int = 4

    # Ablation switches (all on by default; toggle for experiments)
    enable_prescreener: bool = True
    enable_llm_scoring: bool = True
    enable_info_gain: bool = True
    enable_robustness: bool = True
    enable_branch_logging: bool = True
    enable_shadow_dryrun: bool = False   # reserved, not implemented

    # Adaptive trigger thresholds
    early_accept_threshold: float = 0.85
    failure_streak_trigger: int = 1
    confidence_risk_floor: float = 0.20

    # Scoring weights
    lambda_cost: float = 0.10
    mu_risk: float = 0.35
    gamma_info: float = 0.15
    eta_robust: float = 0.25

    # Prescreener
    heuristic_keep_topk: int = 2

    # LLM call settings
    temperature_generate: float = 0.2
    temperature_score: float = 0.0
    max_terminal_chars: int = 6000


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class FactualPlanEstimate:
    confidence: float
    risk: float
    task_type: str
    rationale: str


@dataclass
class CandidatePlan:
    name: str
    rationale: str
    commands: List[Command]
    expected_observation: str = ""
    heuristic_score: float = 0.0   # set by prescreener
    success: float = 0.0
    cost: float = 0.0
    risk: float = 0.0
    info_gain: float = 0.0
    robustness: float = 0.0
    score: float = 0.0
    provenance: str = "generated"  # "factual" | "generated"


@dataclass
class PlannerStats:
    """Per-episode CF overhead — kept separate from main task token counters."""
    planner_mode: str = "skip"
    planner_triggered: bool = False
    n_candidates_raw: int = 0
    n_candidates_after_prescreen: int = 0
    changed_plan: bool = False
    planner_latency_ms: float = 0.0
    planner_extra_llm_calls: int = 0
    planner_extra_input_tokens: int = 0
    planner_extra_output_tokens: int = 0
    planner_extra_cost_usd: float = 0.0


@dataclass
class PlannerResult:
    selected: CandidatePlan
    candidates: List[CandidatePlan]
    mode: str
    changed: bool
    rationale: str
    stats: PlannerStats


# ---------------------------------------------------------------------------
# Planner
# ---------------------------------------------------------------------------

class CounterfactualPlanner:
    """
    Adaptive harness-level counterfactual planner.

    Decision flow per episode:
      _estimate_factual_plan  →  _decide_mode  →  (skip | light | full)
        → _generate_counterfactual_candidates
        → [_prescreen_candidates]
        → [_score_candidates | _heuristic_score_only]
        → _apply_completion_guardrails
        → select max-score candidate
    """

    # Completion-guardrail penalties/bonuses (v1)
    _LATE_STAGE_PENALTY: float = 0.35
    _REVERIFICATION_PENALTY: float = 0.20
    _FACTUAL_COMPLETION_BONUS: float = 0.12

    def __init__(
        self,
        model_name: str,
        api_base: Optional[str] = None,
        api_key: Optional[str] = None,
        config: Optional[CFPlannerConfig] = None,
    ) -> None:
        self.model_name = model_name
        self.api_base = api_base
        self.api_key = api_key
        self.config = config or CFPlannerConfig()

    # ------------------------------------------------------------------
    # Public entry point
    # ------------------------------------------------------------------

    async def select(
        self,
        original_instruction: str,
        terminal_state: str,
        current_prompt: str,
        analysis: str,
        plan: str,
        original_commands: List[Command],
        episode: int,
        failure_streak: int = 0,
    ) -> PlannerResult:
        start = time.perf_counter()
        stats = PlannerStats()

        factual = CandidatePlan(
            name="factual_model_plan",
            rationale=(plan or analysis or "Original model plan").strip(),
            commands=original_commands,
            expected_observation="The observation expected by the factual model plan.",
            provenance="factual",
        )

        if self.config.mode == "off" or not original_commands:
            stats.planner_latency_ms = (time.perf_counter() - start) * 1000.0
            return PlannerResult(
                selected=factual,
                candidates=[factual],
                mode="skip",
                changed=False,
                rationale="Planner disabled or no commands.",
                stats=stats,
            )

        try:
            estimate, u1 = await self._estimate_factual_plan(
                original_instruction, terminal_state, analysis, plan, original_commands
            )
            stats.planner_extra_llm_calls += 1
            stats.planner_extra_input_tokens += u1["prompt_tokens"]
            stats.planner_extra_output_tokens += u1["completion_tokens"]
            stats.planner_extra_cost_usd += u1["cost_usd"]

            mode = self._decide_mode(estimate, failure_streak, episode)
            stats.planner_mode = mode
            stats.planner_triggered = mode != "skip"

            if mode == "skip":
                stats.planner_latency_ms = (time.perf_counter() - start) * 1000.0
                return PlannerResult(
                    selected=factual,
                    candidates=[factual],
                    mode=mode,
                    changed=False,
                    rationale=(
                        f"Planner skipped. confidence={estimate.confidence:.2f}, "
                        f"risk={estimate.risk:.2f}, task_type={estimate.task_type}."
                    ),
                    stats=stats,
                )

            max_candidates = (
                self.config.max_candidates_light
                if mode == "light"
                else self.config.max_candidates_full
            )

            candidates = [factual]
            generated, u2 = await self._generate_counterfactual_candidates(
                original_instruction, terminal_state, current_prompt,
                analysis, plan, original_commands,
                max_new=max(0, max_candidates - 1),
                mode=mode,
            )
            stats.planner_extra_llm_calls += 1
            stats.planner_extra_input_tokens += u2["prompt_tokens"]
            stats.planner_extra_output_tokens += u2["completion_tokens"]
            stats.planner_extra_cost_usd += u2["cost_usd"]
            candidates.extend(generated)
            stats.n_candidates_raw = len(candidates)

            if self.config.enable_prescreener and len(candidates) > 1:
                candidates = self._prescreen_candidates(
                    candidates, terminal_state, estimate,
                    keep_topk=max(1, self.config.heuristic_keep_topk),
                )
            stats.n_candidates_after_prescreen = len(candidates)

            if self.config.enable_llm_scoring and len(candidates) > 1:
                candidates, u3 = await self._score_candidates(
                    original_instruction, terminal_state, candidates, mode
                )
                stats.planner_extra_llm_calls += 1
                stats.planner_extra_input_tokens += u3["prompt_tokens"]
                stats.planner_extra_output_tokens += u3["completion_tokens"]
                stats.planner_extra_cost_usd += u3["cost_usd"]
            else:
                candidates = self._heuristic_score_only(candidates, mode)

            context_text = self._build_context_text(
                original_instruction, terminal_state, current_prompt, analysis, plan
            )
            self._apply_completion_guardrails(candidates, factual.name, context_text)

            selected = max(candidates, key=lambda c: c.score)
            stats.changed_plan = selected.name != factual.name
            stats.planner_latency_ms = (time.perf_counter() - start) * 1000.0

            return PlannerResult(
                selected=selected,
                candidates=candidates,
                mode=mode,
                changed=stats.changed_plan,
                rationale=(
                    f"Selected `{selected.name}` in {mode} mode "
                    f"(score={selected.score:.3f}). "
                    f"Factual confidence={estimate.confidence:.2f}, "
                    f"risk={estimate.risk:.2f}, task_type={estimate.task_type}."
                ),
                stats=stats,
            )

        except Exception as exc:
            stats.planner_latency_ms = (time.perf_counter() - start) * 1000.0
            factual.rationale += f"\n[CounterfactualPlanner failed open: {exc}]"
            return PlannerResult(
                selected=factual,
                candidates=[factual],
                mode="skip",
                changed=False,
                rationale=f"CounterfactualPlanner failed open: {exc}",
                stats=stats,
            )

    # ------------------------------------------------------------------
    # Mode decision
    # ------------------------------------------------------------------

    def _decide_mode(
        self,
        estimate: FactualPlanEstimate,
        failure_streak: int,
        episode: int,
    ) -> str:
        cfg = self.config
        if cfg.mode == "always_light":
            return "light"
        if cfg.mode == "always_full":
            return "full"
        if cfg.mode != "adaptive":
            return "skip"

        # High-confidence, low-risk plan → skip
        if (
            estimate.confidence >= cfg.early_accept_threshold
            and estimate.risk <= cfg.confidence_risk_floor
        ):
            return "skip"

        # After failures → aggressive replanning
        if failure_streak >= cfg.failure_streak_trigger:
            return "full"

        # First episode → light exploration
        if episode <= 1:
            return "light"

        # Task types that benefit from full replanning
        if estimate.task_type in {"debugging", "environment_setup", "visual", "build_test"}:
            return "full"

        if estimate.risk >= 0.5:
            return "full"

        return "light"

    # ------------------------------------------------------------------
    # Step 1: estimate factual plan quality
    # ------------------------------------------------------------------

    async def _estimate_factual_plan(
        self,
        original_instruction: str,
        terminal_state: str,
        analysis: str,
        plan: str,
        original_commands: List[Command],
    ) -> tuple[FactualPlanEstimate, Dict[str, Any]]:
        prompt = f"""You are estimating whether a terminal agent's current factual plan should be
accepted as-is, lightly replanned, or fully replanned.

Original task:
{original_instruction}

Current terminal state:
{terminal_state[-self.config.max_terminal_chars:]}

Model analysis:
{analysis}

Model plan:
{plan}

Commands:
{self._commands_to_text(original_commands)}

Return strict JSON only:
{{
  "confidence": 0.0,
  "risk": 0.0,
  "task_type": "one of [simple_script, debugging, environment_setup, visual, build_test, general]",
  "rationale": "brief explanation"
}}"""
        data, usage = await self._json_completion(prompt, self.config.temperature_score)
        estimate = FactualPlanEstimate(
            confidence=self._clip01(data.get("confidence", 0.5)),
            risk=self._clip01(data.get("risk", 0.5)),
            task_type=str(data.get("task_type", "general")),
            rationale=str(data.get("rationale", "")),
        )
        return estimate, usage

    # ------------------------------------------------------------------
    # Step 2: generate candidates
    # ------------------------------------------------------------------

    async def _generate_counterfactual_candidates(
        self,
        original_instruction: str,
        terminal_state: str,
        current_prompt: str,
        analysis: str,
        plan: str,
        original_commands: List[Command],
        max_new: int,
        mode: str,
    ) -> tuple[List[CandidatePlan], Dict[str, Any]]:
        if max_new <= 0:
            return [], {"prompt_tokens": 0, "completion_tokens": 0, "cost_usd": 0.0}

        context_text = self._build_context_text(
            original_instruction, terminal_state, current_prompt, analysis, plan
        )
        guidance = self._build_progress_guidance(context_text)

        prompt = f"""You are a counterfactual workflow generator for a terminal agent harness.
Generate up to {max_new} executable alternative terminal workflows that may
outperform the factual plan. Use concise, high-value alternatives only.

Planning mode: {mode}
Original task:
{original_instruction}

Current terminal state:
{terminal_state[-self.config.max_terminal_chars:]}

Model analysis:
{analysis}

Model plan:
{plan}

Factual commands:
{self._commands_to_text(original_commands)}

Progress guidance:
{guidance}

Prefer alternatives such as: inspect-first, test-first, minimal-fix,
verification-first, multimodal-first (if visual files matter).

Return strict JSON only:
{{
  "candidates": [
    {{
      "name": "short_name",
      "rationale": "why this branch may be better",
      "expected_observation": "what should happen if executed",
      "commands": [
        {{"keystrokes": "command text ending with newline if needed", "duration": 1.0}}
      ]
    }}
  ]
}}"""
        data, usage = await self._json_completion(prompt, self.config.temperature_generate)
        out: List[CandidatePlan] = []
        for item in data.get("candidates", []):
            commands: List[Command] = []
            for c in item.get("commands", []):
                ks = str(c.get("keystrokes", ""))
                if not ks:
                    continue
                commands.append(
                    Command(keystrokes=ks, duration_sec=min(float(c.get("duration", 1.0)), 60))
                )
            if commands:
                out.append(
                    CandidatePlan(
                        name=str(item.get("name", "counterfactual_plan")),
                        rationale=str(item.get("rationale", "")),
                        expected_observation=str(item.get("expected_observation", "")),
                        commands=commands,
                        provenance="generated",
                    )
                )
        return out, usage

    # ------------------------------------------------------------------
    # Step 3: prescreener (cheap heuristic filter)
    # ------------------------------------------------------------------

    def _prescreen_candidates(
        self,
        candidates: List[CandidatePlan],
        terminal_state: str,
        estimate: FactualPlanEstimate,
        keep_topk: int,
    ) -> List[CandidatePlan]:
        """Cheap heuristic filter before expensive LLM scoring. Always keeps factual plan."""
        factual = [c for c in candidates if c.provenance == "factual"]
        others = [c for c in candidates if c.provenance != "factual"]
        terminal_lower = terminal_state.lower()

        for cand in others:
            cmd_text = self._commands_to_text(cand.commands).lower()
            score = 0.0

            if any(k in cmd_text for k in ["ls", "find", "tree", "cat ", "grep", "pytest", "file "]):
                score += 0.20
            if any(k in cmd_text for k in ["pip install", "apt install", "apt-get install", "brew install"]):
                score -= 0.35
            if any(k in cmd_text for k in ["rm -rf", "sudo", "chmod -r", "git reset --hard"]):
                score -= 0.50
            score -= 0.02 * len(cand.commands)
            score -= 0.002 * len(cmd_text)

            if any(s in terminal_lower for s in ["traceback", "error", "failed"]):
                if any(k in cmd_text for k in ["pytest", "python -m", "make test", "npm test"]):
                    score += 0.20

            if any(k in terminal_lower for k in [".png", ".jpg", ".jpeg", ".pdf"]):
                if "image" in cand.name.lower() or "visual" in cand.rationale.lower():
                    score += 0.15

            score -= 0.25 * max(0.0, estimate.confidence - 0.70)
            cand.heuristic_score = score

        others = sorted(others, key=lambda c: c.heuristic_score, reverse=True)[:keep_topk]
        return factual + others

    # ------------------------------------------------------------------
    # Step 4a: LLM scoring
    # ------------------------------------------------------------------

    async def _score_candidates(
        self,
        original_instruction: str,
        terminal_state: str,
        candidates: List[CandidatePlan],
        mode: str,
    ) -> tuple[List[CandidatePlan], Dict[str, Any]]:
        payload = [
            {
                "id": idx,
                "name": c.name,
                "rationale": c.rationale,
                "expected_observation": c.expected_observation,
                "heuristic_score": c.heuristic_score,
                "commands": [
                    {"keystrokes": cmd.keystrokes, "duration": cmd.duration_sec}
                    for cmd in c.commands
                ],
            }
            for idx, c in enumerate(candidates)
        ]

        metric_text = (
            "Estimate success and risk only."
            if mode == "light"
            else "Estimate success, cost, risk, information gain, and robustness."
        )

        prompt = f"""You are scoring counterfactual terminal workflows before execution.
{metric_text}

Original task:
{original_instruction}

Current terminal state:
{terminal_state[-self.config.max_terminal_chars:]}

Candidates:
{json.dumps(payload, indent=2)}

Return strict JSON only:
{{
  "scores": [
    {{
      "id": 0,
      "success": 0.0,
      "cost": 0.0,
      "risk": 0.0,
      "info_gain": 0.0,
      "robustness": 0.0,
      "rationale": "brief explanation"
    }}
  ]
}}"""
        data, usage = await self._json_completion(prompt, self.config.temperature_score)
        id_to_score = {int(x["id"]): x for x in data.get("scores", []) if "id" in x}

        for idx, c in enumerate(candidates):
            s = id_to_score.get(idx, {})
            c.success = self._clip01(s.get("success", 0.5))
            c.cost = self._clip01(s.get("cost", 0.5))
            c.risk = self._clip01(s.get("risk", 0.5))
            c.info_gain = self._clip01(s.get("info_gain", 0.0))
            c.robustness = self._clip01(s.get("robustness", 0.0))
            c.score = self._compose_score(c, mode)
            if s.get("rationale"):
                c.rationale += f"\nScore rationale: {s['rationale']}"

        return candidates, usage

    # ------------------------------------------------------------------
    # Step 4b: heuristic-only scoring (when LLM scoring is disabled)
    # ------------------------------------------------------------------

    def _heuristic_score_only(
        self, candidates: List[CandidatePlan], mode: str
    ) -> List[CandidatePlan]:
        for c in candidates:
            c.success = self._clip01(0.5 + c.heuristic_score)
            c.risk = self._clip01(0.4 - 0.5 * c.heuristic_score)
            c.cost = self._clip01(0.1 + 0.05 * len(c.commands))
            c.info_gain = self._clip01(max(0.0, c.heuristic_score))
            c.robustness = self._clip01(0.5 + 0.5 * c.heuristic_score)
            c.score = self._compose_score(c, mode)
        return candidates

    def _compose_score(self, c: CandidatePlan, mode: str) -> float:
        cfg = self.config
        if mode == "light":
            return c.success - cfg.mu_risk * c.risk
        score = c.success - cfg.lambda_cost * c.cost - cfg.mu_risk * c.risk
        if cfg.enable_info_gain:
            score += cfg.gamma_info * c.info_gain
        if cfg.enable_robustness:
            score += cfg.eta_robust * c.robustness
        return score

    # ------------------------------------------------------------------
    # Step 5: completion guardrails (v1)
    # Bias toward convergence when task is nearly done to avoid detours.
    # ------------------------------------------------------------------

    def _apply_completion_guardrails(
        self,
        candidates: List[CandidatePlan],
        factual_name: str,
        context_text: str,
    ) -> None:
        if not self._has_strong_completion_signals(context_text):
            return
        packaging_blocker = self._mentions_packaging_blocker(context_text)
        for c in candidates:
            cmd_text = self._commands_to_text(c.commands).lower()
            if c.name == factual_name:
                if not self._introduces_packaging_detour(cmd_text):
                    c.score += self._FACTUAL_COMPLETION_BONUS
                continue
            if not packaging_blocker and self._introduces_packaging_detour(cmd_text):
                c.score -= self._LATE_STAGE_PENALTY
                c.rationale += "\nHeuristic: penalized for packaging detour after strong completion signals."
            if self._looks_like_reverification_loop(cmd_text):
                c.score -= self._REVERIFICATION_PENALTY
                c.rationale += "\nHeuristic: penalized for redundant late-stage reverification."

    # ------------------------------------------------------------------
    # LLM call
    # ------------------------------------------------------------------

    async def _json_completion(
        self, prompt: str, temperature: float
    ) -> tuple[dict[str, Any], Dict[str, Any]]:
        kwargs: dict[str, Any] = {
            "model": self.model_name,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": temperature,
            "timeout": 900,
            "drop_params": True,
        }
        kwargs.update(
            build_reasoning_request_overrides(
                self.model_name,
                reasoning_effort=None,
                include_reasoning_effort=False,
            )
        )
        apply_reasoning_temperature_rules(self.model_name, kwargs)
        if self.api_base:
            kwargs["api_base"] = self.api_base
        if self.api_key:
            kwargs["api_key"] = self.api_key

        response = await litellm.acompletion(**kwargs)
        message = response["choices"][0]["message"]["content"]
        raw = response.get("usage") or {}
        cost = 0.0
        try:
            cost = litellm.completion_cost(completion_response=response) or 0.0
        except Exception:
            pass
        usage = {
            "prompt_tokens": int(raw.get("prompt_tokens", 0) or 0),
            "completion_tokens": int(raw.get("completion_tokens", 0) or 0),
            "cost_usd": cost,
        }
        return self._safe_json_loads(message), usage

    # ------------------------------------------------------------------
    # Static helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _build_context_text(
        original_instruction: str,
        terminal_state: str,
        current_prompt: str,
        analysis: str,
        plan: str,
    ) -> str:
        return "\n\n".join(
            p for p in [original_instruction, terminal_state, current_prompt, analysis, plan] if p
        ).lower()

    def _build_progress_guidance(self, context_text: str) -> str:
        if self._has_strong_completion_signals(context_text):
            return (
                "The task appears close to completion. Prefer minimal finishing "
                "workflows that preserve the current successful path. Avoid "
                "opening new packaging, environment, or smoke-test branches "
                "unless the current context already shows a blocker there."
            )
        return (
            "Prefer workflows that add new information or reduce irreversible risk. "
            "Avoid redundant inspection unless it directly resolves an active blocker."
        )

    @staticmethod
    def _has_strong_completion_signals(context_text: str) -> bool:
        signals = [
            "all constraints satisfied", "roundtrip test passed",
            "directories are identical", "same md5", "checksums are identical",
            "exact reconstruction", "everything is working correctly",
            "decompression completed successfully", "uv sync works",
            "compression completed", "verified the roundtrip works correctly",
        ]
        return sum(1 for s in signals if s in context_text) >= 2

    @staticmethod
    def _mentions_packaging_blocker(context_text: str) -> bool:
        return any(t in context_text for t in [
            "hatchling", "build backend returned an error",
            "unable to determine which files to ship", "build_editable",
            "uv run", "editable", "tool.hatch.build.targets.wheel",
            "metadata not found", "pyproject.toml needs",
        ])

    @staticmethod
    def _introduces_packaging_detour(command_text: str) -> bool:
        return any(t in command_text for t in [
            "uv run", "uv sync", "pyproject.toml", "hatchling",
            "build-system", "tool.hatch.build.targets.wheel",
            "editable", "wheel", "src/",
        ])

    @staticmethod
    def _looks_like_reverification_loop(command_text: str) -> bool:
        return any(t in command_text for t in [
            "diff ", "md5sum", "sha256sum", "verify.py", "validate.py",
            "smoke_test", "quick_test", "head -",
            "cat /app/pyproject.toml", "ls -la /app",
        ])

    @staticmethod
    def _safe_json_loads(text: str) -> dict[str, Any]:
        text = text.strip()
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            match = re.search(r"\{.*\}", text, flags=re.DOTALL)
            if not match:
                raise
            return json.loads(match.group(0))

    @staticmethod
    def _commands_to_text(commands: List[Command]) -> str:
        return "\n".join(
            f"- duration={c.duration_sec}: {c.keystrokes!r}" for c in commands
        )

    @staticmethod
    def _clip01(x: Any) -> float:
        try:
            return max(0.0, min(1.0, float(x)))
        except Exception:
            return 0.5

    @staticmethod
    def planner_stats_to_dict(stats: PlannerStats) -> dict[str, Any]:
        return asdict(stats)
