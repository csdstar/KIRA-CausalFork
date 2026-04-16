import json
import re
import time
from dataclasses import dataclass
from typing import Any, List, Optional

import litellm
from harbor.agents.terminus_2.terminus_2 import Command
from harbor.models.metric import UsageInfo
from terminus_kira.reasoning_controls import (
    apply_reasoning_temperature_rules,
    build_reasoning_request_overrides,
)


@dataclass
class CandidatePlan:
    name: str
    rationale: str
    commands: List[Command]
    expected_observation: str = ""
    success: float = 0.0
    cost: float = 0.0
    risk: float = 0.0
    info_gain: float = 0.0
    robustness: float = 0.0
    score: float = 0.0


@dataclass
class PlannerResult:
    selected: CandidatePlan
    candidates: List[CandidatePlan]
    changed: bool
    rationale: str
    usage: UsageInfo | None = None
    api_request_times_ms: List[float] | None = None


class CounterfactualPlanner:
    """
    Harness-level counterfactual planner.

    It intercepts a proposed execute_commands action and compares it against
    alternative workflows before the real terminal execution happens.
    """

    def __init__(
        self,
        model_name: str,
        temperature: float = 0.2,
        max_candidates: int = 4,
        lambda_cost: float = 0.10,
        mu_risk: float = 0.35,
        gamma_info: float = 0.15,
        eta_robust: float = 0.25,
        api_base: Optional[str] = None,
        api_key: Optional[str] = None,
        trigger_mode: str = "risk",  # "always", "risk", or "off"
    ) -> None:
        self.model_name = model_name
        self.temperature = temperature
        self.max_candidates = max_candidates
        self.lambda_cost = lambda_cost
        self.mu_risk = mu_risk
        self.gamma_info = gamma_info
        self.eta_robust = eta_robust
        self.api_base = api_base
        self.api_key = api_key
        self.trigger_mode = trigger_mode
        self._planner_usage: UsageInfo | None = None
        self._planner_request_times_ms: List[float] = []
        self._late_stage_score_penalty = 0.35
        self._reverification_penalty = 0.20
        self._factual_completion_bonus = 0.12

    def should_trigger(
        self,
        commands: List[Command],
        episode: int,
        is_task_complete: bool = False,
    ) -> bool:
        if self.trigger_mode == "off":
            return False
        if self.trigger_mode == "always":
            return bool(commands)

        if not commands:
            return False

        # Trigger at the first few strategic decisions.
        if episode <= 1:
            return True

        joined = "\n".join(c.keystrokes for c in commands).lower()

        high_risk_patterns = [
            r"\brm\s+-rf\b",
            r"\bsudo\b",
            r"\bapt(-get)?\s+install\b",
            r"\bpip\s+install\b",
            r"\bchmod\s+[-+]x\b",
            r"\bsed\s+-i\b",
            r"\bmv\s+",
            r">\s*[\w./-]+",
            r"\bgit\s+",
            r"\bmake\b",
            r"\bpytest\b",
            r"\bpython(3)?\s+.*\.py\b",
        ]
        return any(re.search(p, joined) for p in high_risk_patterns)

    async def select(
        self,
        original_instruction: str,
        terminal_state: str,
        current_prompt: str,
        analysis: str,
        plan: str,
        original_commands: List[Command],
        episode: int,
    ) -> PlannerResult:
        self._planner_usage = UsageInfo(
            prompt_tokens=0,
            completion_tokens=0,
            cache_tokens=0,
            cost_usd=0.0,
        )
        self._planner_request_times_ms = []
        context_text = self._build_context_text(
            original_instruction=original_instruction,
            terminal_state=terminal_state,
            current_prompt=current_prompt,
            analysis=analysis,
            plan=plan,
        )
        factual = CandidatePlan(
            name="factual_model_plan",
            rationale=plan or analysis,
            commands=original_commands,
            expected_observation="The observation expected by the original model plan.",
        )

        candidates = [factual]

        try:
            proposed = await self._generate_counterfactual_candidates(
                original_instruction=original_instruction,
                terminal_state=terminal_state,
                current_prompt=current_prompt,
                analysis=analysis,
                plan=plan,
                original_commands=original_commands,
            )
            candidates.extend(proposed[: max(0, self.max_candidates - 1)])

            scored = await self._score_candidates(
                original_instruction=original_instruction,
                terminal_state=terminal_state,
                candidates=candidates,
                context_text=context_text,
            )
            self._apply_completion_guardrails(scored, factual.name, context_text)
            selected = max(scored, key=lambda c: c.score)
            changed = selected.name != factual.name

            return PlannerResult(
                selected=selected,
                candidates=scored,
                changed=changed,
                rationale=(
                    f"Selected `{selected.name}` with score={selected.score:.3f}. "
                    f"success={selected.success:.2f}, risk={selected.risk:.2f}, "
                    f"cost={selected.cost:.2f}, info_gain={selected.info_gain:.2f}, "
                    f"robustness={selected.robustness:.2f}."
                ),
                usage=self._planner_usage,
                api_request_times_ms=self._planner_request_times_ms.copy(),
            )

        except Exception as exc:
            # Fail open: never block KIRA if the planner fails.
            factual.rationale += f"\n[CounterfactualPlanner failed open: {exc}]"
            return PlannerResult(
                selected=factual,
                candidates=[factual],
                changed=False,
                rationale=f"CounterfactualPlanner failed open: {exc}",
                usage=self._planner_usage,
                api_request_times_ms=self._planner_request_times_ms.copy(),
            )

    async def _generate_counterfactual_candidates(
        self,
        original_instruction: str,
        terminal_state: str,
        current_prompt: str,
        analysis: str,
        plan: str,
        original_commands: List[Command],
    ) -> List[CandidatePlan]:
        original_cmd_text = self._commands_to_text(original_commands)
        guidance = self._build_progress_guidance(
            self._build_context_text(
                original_instruction=original_instruction,
                terminal_state=terminal_state,
                current_prompt=current_prompt,
                analysis=analysis,
                plan=plan,
            )
        )

        prompt = f"""
You are a counterfactual workflow planner for a terminal agent harness.

The agent proposed one factual command plan. Generate alternative executable
terminal workflows that could be better under plausible hidden-test or
environment variations.

Original task:
{original_instruction}

Current terminal state:
{terminal_state[-6000:]}

Model analysis:
{analysis}

Model plan:
{plan}

Factual commands:
{original_cmd_text}

Progress guidance:
{guidance}

Generate up to {self.max_candidates - 1} alternatives. Prefer useful workflow
differences, for example:
- inspect-first: gather missing evidence before editing
- test-first: run a minimal validation before changing code
- minimal-fix: make a smaller safer change
- verification-first: add or run checks before completion
- multimodal-first: use image inspection if visual files are central

Return strict JSON only:
{{
  "candidates": [
    {{
      "name": "short_name",
      "rationale": "why this counterfactual branch may be better",
      "expected_observation": "what should happen if this branch is executed",
      "commands": [
        {{"keystrokes": "command text ending with newline if needed", "duration": 1.0}}
      ]
    }}
  ]
}}
"""

        data = await self._json_completion(prompt)
        out: List[CandidatePlan] = []

        for item in data.get("candidates", []):
            commands: List[Command] = []
            for c in item.get("commands", []):
                keystrokes = str(c.get("keystrokes", ""))
                if not keystrokes:
                    continue
                duration = float(c.get("duration", 1.0))
                commands.append(Command(keystrokes=keystrokes, duration_sec=min(duration, 60)))

            if commands:
                out.append(
                    CandidatePlan(
                        name=str(item.get("name", "counterfactual_plan")),
                        rationale=str(item.get("rationale", "")),
                        expected_observation=str(item.get("expected_observation", "")),
                        commands=commands,
                    )
                )

        return out

    async def _score_candidates(
        self,
        original_instruction: str,
        terminal_state: str,
        candidates: List[CandidatePlan],
        context_text: str,
    ) -> List[CandidatePlan]:
        candidate_payload = []
        for idx, c in enumerate(candidates):
            candidate_payload.append(
                {
                    "id": idx,
                    "name": c.name,
                    "rationale": c.rationale,
                    "expected_observation": c.expected_observation,
                    "commands": [
                        {"keystrokes": cmd.keystrokes, "duration": cmd.duration_sec}
                        for cmd in c.commands
                    ],
                }
            )

        prompt = f"""
You are scoring counterfactual terminal workflows before execution.

Original task:
{original_instruction}

Current terminal state:
{terminal_state[-6000:]}

Progress guidance:
{self._build_progress_guidance(context_text)}

Candidates:
{json.dumps(candidate_payload, indent=2)}

For each candidate, estimate:
- success: probability of making progress toward final task success, 0 to 1
- cost: expected time/token/tool overhead, 0 to 1, lower is better
- risk: probability of harmful or irreversible error, 0 to 1, lower is better
- info_gain: expected useful diagnostic information, 0 to 1
- robustness: likelihood the plan works under hidden tests/path/input changes, 0 to 1

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
}}
"""
        data = await self._json_completion(prompt)
        id_to_score = {int(x["id"]): x for x in data.get("scores", []) if "id" in x}

        for idx, c in enumerate(candidates):
            s = id_to_score.get(idx, {})
            c.success = self._clip01(s.get("success", 0.5))
            c.cost = self._clip01(s.get("cost", 0.5))
            c.risk = self._clip01(s.get("risk", 0.5))
            c.info_gain = self._clip01(s.get("info_gain", 0.0))
            c.robustness = self._clip01(s.get("robustness", 0.0))
            c.score = (
                c.success
                - self.lambda_cost * c.cost
                - self.mu_risk * c.risk
                + self.gamma_info * c.info_gain
                + self.eta_robust * c.robustness
            )
            if s.get("rationale"):
                c.rationale += f"\nScore rationale: {s['rationale']}"

        return candidates

    def _apply_completion_guardrails(
        self,
        candidates: List[CandidatePlan],
        factual_name: str,
        context_text: str,
    ) -> None:
        """Bias the planner toward convergence once the task looks nearly done.

        This keeps CF helpful in early exploration, but reduces the chance that
        late-stage "verify even more" branches open a brand-new subproblem such
        as packaging or build-system work after the core task is already solved.
        """
        if not self._has_strong_completion_signals(context_text):
            return

        packaging_blocker_present = self._mentions_packaging_blocker(context_text)

        for candidate in candidates:
            command_text = self._commands_to_text(candidate.commands).lower()

            if candidate.name == factual_name:
                if not self._introduces_packaging_detour(command_text):
                    candidate.score += self._factual_completion_bonus
                continue

            if (
                not packaging_blocker_present
                and self._introduces_packaging_detour(command_text)
            ):
                candidate.score -= self._late_stage_score_penalty
                candidate.rationale += (
                    "\nHeuristic adjustment: penalized because the task already "
                    "shows strong success signals, and this branch introduces a "
                    "new packaging/build-system detour."
                )

            if self._looks_like_reverification_loop(command_text):
                candidate.score -= self._reverification_penalty
                candidate.rationale += (
                    "\nHeuristic adjustment: penalized for redundant late-stage "
                    "reverification after strong completion signals."
                )

    @staticmethod
    def _build_context_text(
        original_instruction: str,
        terminal_state: str,
        current_prompt: str,
        analysis: str,
        plan: str,
    ) -> str:
        return "\n\n".join(
            part
            for part in [
                original_instruction,
                terminal_state,
                current_prompt,
                analysis,
                plan,
            ]
            if part
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
            "Prefer workflows that add new information or reduce irreversible "
            "risk. Avoid redundant inspection unless it directly resolves an "
            "active blocker."
        )

    @staticmethod
    def _has_strong_completion_signals(context_text: str) -> bool:
        success_signals = [
            "all constraints satisfied",
            "roundtrip test passed",
            "directories are identical",
            "same md5",
            "checksums are identical",
            "exact reconstruction",
            "everything is working correctly",
            "decompression completed successfully",
            "uv sync works",
            "compression completed",
            "verified the roundtrip works correctly",
        ]
        matches = sum(1 for signal in success_signals if signal in context_text)
        return matches >= 2

    @staticmethod
    def _mentions_packaging_blocker(context_text: str) -> bool:
        packaging_blockers = [
            "hatchling",
            "build backend returned an error",
            "unable to determine which files to ship",
            "build_editable",
            "uv run",
            "editable",
            "tool.hatch.build.targets.wheel",
            "metadata not found",
            "pyproject.toml needs",
        ]
        return any(token in context_text for token in packaging_blockers)

    @staticmethod
    def _introduces_packaging_detour(command_text: str) -> bool:
        detour_tokens = [
            "uv run",
            "uv sync",
            "pyproject.toml",
            "hatchling",
            "build-system",
            "tool.hatch.build.targets.wheel",
            "editable",
            "wheel",
            "src/",
        ]
        return any(token in command_text for token in detour_tokens)

    @staticmethod
    def _looks_like_reverification_loop(command_text: str) -> bool:
        reverification_tokens = [
            "diff ",
            "md5sum",
            "sha256sum",
            "verify.py",
            "validate.py",
            "smoke_test",
            "quick_test",
            "head -",
            "cat /app/pyproject.toml",
            "ls -la /app",
        ]
        return any(token in command_text for token in reverification_tokens)

    async def _json_completion(self, prompt: str) -> dict[str, Any]:
        kwargs: dict[str, Any] = {
            "model": self.model_name,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": self.temperature,
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

        start_time = time.time()
        response = await litellm.acompletion(**kwargs)
        end_time = time.time()
        self._planner_request_times_ms.append((end_time - start_time) * 1000)
        self._update_planner_usage(self._extract_usage_info(response))
        text = response["choices"][0]["message"]["content"]
        return self._safe_json_loads(text)

    def _extract_usage_info(self, response) -> UsageInfo | None:
        """Extract usage info from a LiteLLM completion response."""
        try:
            usage = response.usage
            if usage:
                cost = 0.0
                try:
                    cost = litellm.completion_cost(completion_response=response) or 0.0
                except Exception:
                    pass
                return UsageInfo(
                    prompt_tokens=usage.prompt_tokens or 0,
                    completion_tokens=usage.completion_tokens or 0,
                    cache_tokens=getattr(usage, "cache_read_input_tokens", 0) or 0,
                    cost_usd=cost,
                )
        except (AttributeError, TypeError):
            pass
        return None

    def _update_planner_usage(self, usage: UsageInfo | None) -> None:
        """Accumulate planner-side usage so CF cost is visible in trial stats."""
        if usage is None:
            return
        if self._planner_usage is None:
            self._planner_usage = UsageInfo(
                prompt_tokens=0,
                completion_tokens=0,
                cache_tokens=0,
                cost_usd=0.0,
            )
        self._planner_usage.prompt_tokens += usage.prompt_tokens
        self._planner_usage.completion_tokens += usage.completion_tokens
        self._planner_usage.cache_tokens += usage.cache_tokens
        self._planner_usage.cost_usd += usage.cost_usd

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
