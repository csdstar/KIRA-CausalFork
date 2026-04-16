from pathlib import Path

from harbor.llms.base import LLMResponse
from harbor.llms.chat import Chat
from harbor.agents.terminus_2.tmux_session import TmuxSession

from terminus_kira.terminus_kira import TerminusKira, ImageReadRequest
from terminus_kira.counterfactual_planner import CounterfactualPlanner


class TerminusKiraCF(TerminusKira):
    """
    Counterfactual-planning extension of Terminus-KIRA.

    Inserted point:
    LLM native tool call -> parse execute_commands -> CounterfactualPlanner
    -> selected commands -> tmux execution.
    """

    @staticmethod
    def name() -> str:
        return "terminus-kira-cf"

    def version(self) -> str | None:
        return "1.0.0-cfplanner"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        connection_kwargs = self._get_litellm_connection_kwargs()

        self._cf_planner = CounterfactualPlanner(
            model_name=self._model_name,
            temperature=0.2,
            max_candidates=4,
            lambda_cost=0.10,
            mu_risk=0.35,
            gamma_info=0.15,
            eta_robust=0.25,
            api_base=connection_kwargs.get("api_base"),
            api_key=connection_kwargs.get("api_key"),
            trigger_mode="risk",
        )

    async def _handle_llm_interaction(
        self,
        chat: Chat,
        prompt: str,
        logging_paths: tuple[Path | None, Path | None, Path | None],
        original_instruction: str = "",
        session: TmuxSession | None = None,
    ) -> tuple[
        list,
        bool,
        str,
        str,
        str,
        LLMResponse,
        ImageReadRequest | None,
    ]:
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

        # Only intervene before real shell execution.
        # Do not override image_read or task_complete confirmation behavior.
        if (
            session is not None
            and image_read is None
            and commands
            and not is_task_complete
            and self._cf_planner.should_trigger(
                commands=commands,
                episode=getattr(self, "_n_episodes", 0),
                is_task_complete=is_task_complete,
            )
        ):
            try:
                terminal_state = await self._with_block_timeout(
                    session.capture_pane(capture_entire=False)
                )

                result = await self._cf_planner.select(
                    original_instruction=original_instruction,
                    terminal_state=terminal_state or "",
                    current_prompt=prompt,
                    analysis=analysis,
                    plan=plan,
                    original_commands=commands,
                    episode=getattr(self, "_n_episodes", 0),
                )

                # Count counterfactual-planner API usage as part of the same
                # agent step so trial-level token/cost stats reflect the real
                # cost of running CF mode.
                if result.usage is not None:
                    chat._cumulative_input_tokens += result.usage.prompt_tokens
                    chat._cumulative_output_tokens += result.usage.completion_tokens
                    chat._cumulative_cache_tokens += result.usage.cache_tokens
                    chat._cumulative_cost += result.usage.cost_usd
                if result.api_request_times_ms:
                    self._api_request_times.extend(result.api_request_times_ms)

                if result.changed:
                    commands = result.selected.commands

                cf_summary = self._format_cf_summary(result)
                analysis = f"{analysis}\n\n[CounterfactualPlanner]\n{cf_summary}"
                plan = (
                    f"{plan}\n\n[Selected counterfactual workflow]\n"
                    f"{result.selected.rationale}"
                )

            except Exception as exc:
                # Fail open to preserve KIRA behavior.
                analysis = (
                    f"{analysis}\n\n[CounterfactualPlanner failed open: {exc}]"
                )

        return (
            commands,
            is_task_complete,
            feedback,
            analysis,
            plan,
            llm_response,
            image_read,
        )

    @staticmethod
    def _format_cf_summary(result) -> str:
        rows = []
        for c in result.candidates:
            rows.append(
                f"- {c.name}: score={c.score:.3f}, "
                f"success={c.success:.2f}, cost={c.cost:.2f}, "
                f"risk={c.risk:.2f}, info_gain={c.info_gain:.2f}, "
                f"robustness={c.robustness:.2f}"
            )
        return (
            f"{result.rationale}\n"
            f"Changed factual plan: {result.changed}\n"
            f"Candidate comparison:\n" + "\n".join(rows)
        )
