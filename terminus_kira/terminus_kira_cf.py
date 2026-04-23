"""
Terminus-KIRA with adaptive counterfactual planning.

Architecture:
  TerminusKiraCF extends TerminusKira.
  _handle_llm_interaction() calls super() to get the factual plan, then
  invokes CounterfactualPlanner.select() to optionally replace commands.

Token accounting boundary (requirement 3):
  CF planner tokens are accumulated in _cf_total_stats (PlannerStats) and
  are NEVER added to chat._cumulative_* counters. Main task stats remain
  uncontaminated so CF overhead can be measured independently.
"""
import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from harbor.llms.base import LLMResponse
from harbor.llms.chat import Chat
from harbor.agents.terminus_2.tmux_session import TmuxSession
from harbor.agents.terminus_2.terminus_2 import Command

from terminus_kira.terminus_kira import TerminusKira, ImageReadRequest
from terminus_kira.counterfactual_planner import (
    CFPlannerConfig,
    CounterfactualPlanner,
    PlannerResult,
)


@dataclass
class _CFTotals:
    """Accumulated CF overhead across all episodes of one run."""
    llm_calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0
    episodes_triggered: int = 0
    changed_plans: int = 0


# Error patterns used for failure-streak detection
_ERROR_SIGNALS = (
    "error:", "traceback", "exception:", "command not found",
    "no such file", "permission denied", "failed", "syntax error",
)


class TerminusKiraCF(TerminusKira):
    """
    TerminusKira extended with adaptive counterfactual planning.

    Configurable via CFPlannerConfig:
      - mode: off | adaptive | always_light | always_full
      - enable_prescreener / enable_llm_scoring / enable_info_gain /
        enable_robustness / enable_branch_logging
      - max_candidates_light / max_candidates_full
      - early_accept_threshold, failure_streak_trigger, …
    """

    @staticmethod
    def name() -> str:
        return "terminus-kira-cf"

    def version(self) -> str | None:
        return "2.0.0-cf"

    # Preset ablation profiles. Selected via cf_mode kwarg / KIRA_CF_MODE env var.
    # Each maps to a CFPlannerConfig override dict.
    _CF_MODE_PRESETS: dict[str, dict[str, Any]] = {
        "off":                    {"mode": "off"},
        "adaptive":               {"mode": "adaptive"},
        "adaptive_no_scorer":     {"mode": "adaptive", "enable_llm_scoring": False},
        "adaptive_no_prescreen":  {"mode": "adaptive", "enable_prescreener": False},
        "adaptive_no_robust":     {"mode": "adaptive", "enable_robustness": False},
        "adaptive_no_info":       {"mode": "adaptive", "enable_info_gain": False},
        "always_light":           {"mode": "always_light"},
        "always_full":            {"mode": "always_full"},
    }

    def __init__(self, *args, cf_mode: str | None = None, **kwargs):
        super().__init__(*args, **kwargs)

        import os
        cf_mode = cf_mode or os.getenv("KIRA_CF_MODE") or "adaptive"
        if cf_mode not in self._CF_MODE_PRESETS:
            raise ValueError(
                f"Unknown cf_mode={cf_mode!r}. "
                f"Valid: {sorted(self._CF_MODE_PRESETS)}"
            )
        overrides = self._CF_MODE_PRESETS[cf_mode]

        conn = self._get_litellm_connection_kwargs()
        base_cfg: dict[str, Any] = dict(
            mode="adaptive",
            max_candidates_light=2,
            max_candidates_full=4,
            enable_prescreener=True,
            enable_llm_scoring=True,
            enable_info_gain=True,
            enable_robustness=True,
            enable_branch_logging=True,
            early_accept_threshold=0.85,
            failure_streak_trigger=1,
        )
        base_cfg.update(overrides)
        self._cf_mode_name = cf_mode
        self._cf_config = CFPlannerConfig(**base_cfg)
        print(f"[KIRA CF] cf_mode={cf_mode} config={base_cfg}")
        self._cf_planner = CounterfactualPlanner(
            model_name=self._model_name,
            api_base=conn.get("api_base"),
            api_key=conn.get("api_key"),
            config=self._cf_config,
        )
        self._cf_failure_streak: int = 0
        self._cf_totals = _CFTotals()

    # ------------------------------------------------------------------
    # Override: inject CF planning between LLM response and execution
    # ------------------------------------------------------------------

    async def _handle_llm_interaction(
        self,
        chat: Chat,
        prompt: str,
        logging_paths: tuple[Path | None, Path | None, Path | None],
        original_instruction: str = "",
        session: TmuxSession | None = None,
    ) -> tuple[list, bool, str, str, str, LLMResponse, ImageReadRequest | None]:
        (
            commands,
            is_task_complete,
            feedback,
            analysis,
            plan,
            llm_response,
            image_read,
        ) = await super()._handle_llm_interaction(
            chat=chat,
            prompt=prompt,
            logging_paths=logging_paths,
            original_instruction=original_instruction,
            session=session,
        )

        # Only intervene on normal command execution steps.
        # Skip when: no session, image_read in progress, task already complete,
        # or no commands to replace.
        if not (session and image_read is None and commands and not is_task_complete):
            return commands, is_task_complete, feedback, analysis, plan, llm_response, image_read

        try:
            terminal_state = await self._with_block_timeout(
                session.capture_pane(capture_entire=False)
            )
            result: PlannerResult = await self._cf_planner.select(
                original_instruction=original_instruction,
                terminal_state=terminal_state or "",
                current_prompt=prompt,
                analysis=analysis,
                plan=plan,
                original_commands=commands,
                episode=getattr(self, "_n_episodes", 0),
                failure_streak=self._cf_failure_streak,
            )

            # ---- Token isolation boundary (requirement 3) ----
            # Accumulate CF stats separately; do NOT touch chat._cumulative_*.
            s = result.stats
            self._cf_totals.llm_calls += s.planner_extra_llm_calls
            self._cf_totals.input_tokens += s.planner_extra_input_tokens
            self._cf_totals.output_tokens += s.planner_extra_output_tokens
            self._cf_totals.cost_usd += s.planner_extra_cost_usd
            if s.planner_triggered:
                self._cf_totals.episodes_triggered += 1
            if result.changed:
                self._cf_totals.changed_plans += 1
                commands = result.selected.commands

            if self._cf_config.enable_branch_logging:
                self._log_cf_episode(logging_paths, result)

            cf_summary = self._format_cf_summary(result)
            analysis = f"{analysis}\n\n[CounterfactualPlanner]\n{cf_summary}"
            plan = (
                f"{plan}\n\n[Selected workflow]\n"
                f"Mode: {result.mode} | Selected: {result.selected.name}\n"
                f"{result.selected.rationale}"
            )

        except Exception as exc:
            analysis = f"{analysis}\n\n[CounterfactualPlanner failed open: {exc}]"

        return commands, is_task_complete, feedback, analysis, plan, llm_response, image_read

    # ------------------------------------------------------------------
    # Override: track failure streak for adaptive mode trigger
    # ------------------------------------------------------------------

    async def _execute_commands(
        self,
        commands: list[Command],
        session: TmuxSession,
    ) -> tuple[bool, str]:
        timeout_occurred, output = await super()._execute_commands(commands, session)
        lower = output.lower()
        if any(sig in lower for sig in _ERROR_SIGNALS):
            self._cf_failure_streak += 1
        else:
            self._cf_failure_streak = 0
        return timeout_occurred, output

    # ------------------------------------------------------------------
    # Override: print CF summary after run completes
    # ------------------------------------------------------------------

    async def run(self, instruction, environment, context):
        await super().run(instruction, environment, context)
        t = self._cf_totals
        print(
            f"[KIRA CF STATS]"
            f" episodes_triggered={t.episodes_triggered}"
            f" changed_plans={t.changed_plans}"
            f" cf_llm_calls={t.llm_calls}"
            f" cf_input_tokens={t.input_tokens}"
            f" cf_output_tokens={t.output_tokens}"
            f" cf_cost_usd={t.cost_usd:.6f}"
        )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _log_cf_episode(
        self,
        logging_paths: tuple[Path | None, Path | None, Path | None],
        result: PlannerResult,
    ) -> None:
        _, _, response_path = logging_paths
        if response_path is None:
            return
        try:
            cf_path = response_path.parent / (response_path.stem + "_cf.json")
            cf_path.write_text(
                json.dumps(self._serialize_result(result), indent=2, ensure_ascii=False)
            )
        except Exception:
            pass

    def _serialize_result(self, result: PlannerResult) -> dict[str, Any]:
        return {
            "mode": result.mode,
            "changed": result.changed,
            "rationale": result.rationale,
            "stats": asdict(result.stats),
            "candidates": [
                {
                    "name": c.name,
                    "provenance": c.provenance,
                    "heuristic_score": c.heuristic_score,
                    "success": c.success,
                    "cost": c.cost,
                    "risk": c.risk,
                    "info_gain": c.info_gain,
                    "robustness": c.robustness,
                    "score": c.score,
                }
                for c in result.candidates
            ],
        }

    @staticmethod
    def _format_cf_summary(result: PlannerResult) -> str:
        rows = [
            f"- {c.name} [{c.provenance}]: score={c.score:.3f}, "
            f"heuristic={c.heuristic_score:.3f}, success={c.success:.2f}, "
            f"cost={c.cost:.2f}, risk={c.risk:.2f}, "
            f"info_gain={c.info_gain:.2f}, robustness={c.robustness:.2f}"
            for c in result.candidates
        ]
        return (
            f"{result.rationale}\n"
            f"Changed factual plan: {result.changed}\n"
            f"Planner stats: {json.dumps(asdict(result.stats), ensure_ascii=False)}\n"
            f"Candidate comparison:\n" + "\n".join(rows)
        )
