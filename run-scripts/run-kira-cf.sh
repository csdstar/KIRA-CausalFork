#!/usr/bin/env bash
# Run a TerminusKiraCF benchmark with a chosen ablation profile.
#
# Required env (loaded from .env):
#   MODEL_NAME, DATASET, HARBOR_ENV, N_CONCURRENT, N_TASKS, CF_JOB_PREFIX
# Optional env:
#   CF_MODE              one of: off | adaptive | adaptive_no_scorer |
#                                adaptive_no_prescreen | adaptive_no_robust |
#                                adaptive_no_info | always_light | always_full
#                        default: adaptive
#   INCLUDE_TASKS_FILE   path to a file with one task name per line.
#                        Each name will be passed as --include-task-name.
#   EXCLUDE_TASKS_FILE   same shape, passed as --exclude-task-name.
#   INCLUDE_TASK         single task name (added in addition to file selectors)
#   MODEL_INFO, TEMPERATURE, MAX_TURNS, REASONING_EFFORT
#   JOB_NAME             override the auto-generated job name
#
# Examples:
#   CF_MODE=adaptive                     ./run-scripts/run-kira-cf.sh
#   CF_MODE=off                          ./run-scripts/run-kira-cf.sh   # baseline
#   CF_MODE=adaptive_no_scorer           ./run-scripts/run-kira-cf.sh
#   CF_MODE=adaptive INCLUDE_TASKS_FILE=failed.txt ./run-scripts/run-kira-cf.sh

set -euo pipefail

source /home/star/env/miniconda/etc/profile.d/conda.sh
conda activate kira

set -a
source /home/star/project/KIRA-CausalFork/.env
set +a

CF_MODE="${CF_MODE:-adaptive}"

case "$CF_MODE" in
  off|adaptive|adaptive_no_scorer|adaptive_no_prescreen|adaptive_no_robust|adaptive_no_info|always_light|always_full)
    ;;
  *)
    echo "ERROR: unknown CF_MODE=$CF_MODE" >&2
    echo "Valid: off | adaptive | adaptive_no_scorer | adaptive_no_prescreen | adaptive_no_robust | adaptive_no_info | always_light | always_full" >&2
    exit 1
    ;;
esac

JOB_NAME="${JOB_NAME:-${CF_JOB_PREFIX}-${CF_MODE}-$(date +%Y-%m-%d__%H-%M)}"

agent_kwargs=(--agent-kwarg "cf_mode=${CF_MODE}")
if [[ -n "${MODEL_INFO:-}" ]]; then
  agent_kwargs+=(--agent-kwarg "model_info=${MODEL_INFO}")
fi
if [[ -n "${TEMPERATURE:-}" ]]; then
  agent_kwargs+=(--agent-kwarg "temperature=${TEMPERATURE}")
fi
if [[ -n "${MAX_TURNS:-}" ]]; then
  agent_kwargs+=(--agent-kwarg "max_turns=${MAX_TURNS}")
fi
if [[ -n "${REASONING_EFFORT:-}" ]]; then
  agent_kwargs+=(--agent-kwarg "reasoning_effort=${REASONING_EFFORT}")
fi

# Build harbor task selectors from optional include/exclude files.
task_selectors=()
if [[ -n "${INCLUDE_TASK:-}" ]]; then
  task_selectors+=(--include-task-name "${INCLUDE_TASK}")
fi
if [[ -n "${INCLUDE_TASKS_FILE:-}" && -f "${INCLUDE_TASKS_FILE}" ]]; then
  while IFS= read -r line; do
    [[ -z "$line" || "$line" =~ ^# ]] && continue
    task_selectors+=(--include-task-name "$line")
  done < "${INCLUDE_TASKS_FILE}"
fi
if [[ -n "${EXCLUDE_TASKS_FILE:-}" && -f "${EXCLUDE_TASKS_FILE}" ]]; then
  while IFS= read -r line; do
    [[ -z "$line" || "$line" =~ ^# ]] && continue
    task_selectors+=(--exclude-task-name "$line")
  done < "${EXCLUDE_TASKS_FILE}"
fi

echo "=== Launching CF job ==="
echo "  CF_MODE   = ${CF_MODE}"
echo "  JOB_NAME  = ${JOB_NAME}"
echo "  DATASET   = ${DATASET}"
echo "  N_TASKS   = ${N_TASKS}"
echo "  selectors = ${#task_selectors[@]} item(s)"

"$CONDA_PREFIX/bin/harbor" run \
  --dataset "$DATASET" \
  --n-tasks "$N_TASKS" \
  --job-name "$JOB_NAME" \
  --agent-import-path "terminus_kira.terminus_kira_cf:TerminusKiraCF" \
  --model "$MODEL_NAME" \
  --env "$HARBOR_ENV" \
  --n-concurrent "$N_CONCURRENT" \
  "${task_selectors[@]}" \
  "${agent_kwargs[@]}"

# Auto-split tasks into passed/failed/errored-CF lists.
JOB_DIR="jobs/${JOB_NAME}"
SPLITTER="$(dirname "$0")/../scripts/split_job_tasks.sh"
if [[ -d "$JOB_DIR" && -x "$SPLITTER" ]]; then
    "$SPLITTER" "$JOB_DIR" || echo "[warn] split_job_tasks.sh failed (continuing)" >&2
else
    echo "[warn] skipping task split: JOB_DIR=$JOB_DIR  splitter=$SPLITTER" >&2
fi
