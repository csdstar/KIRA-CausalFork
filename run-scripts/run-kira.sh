#!/usr/bin/env bash
set -euo pipefail

source /home/star/env/miniconda/etc/profile.d/conda.sh
conda activate kira

set -a
source /home/star/project/KIRA-CausalFork/.env
set +a

JOB_NAME="${JOB_NAME:-${BASE_JOB_PREFIX}-$(date +%Y-%m-%d__%H-%M)}"

agent_kwargs=()
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

# Optional task selectors (same shape as run-kira-cf.sh):
#   INCLUDE_TASK         single task name
#   INCLUDE_TASKS_FILE   one task name per line
#   EXCLUDE_TASKS_FILE   one task name per line
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

echo "=== Launching base job ==="
echo "  JOB_NAME  = ${JOB_NAME}"
echo "  DATASET   = ${DATASET}"
echo "  N_TASKS   = ${N_TASKS}"
echo "  selectors = ${#task_selectors[@]} item(s)"

"$CONDA_PREFIX/bin/harbor" run \
  --dataset "$DATASET" \
  --n-tasks "$N_TASKS" \
  --job-name "$JOB_NAME" \
  --agent-import-path "terminus_kira.terminus_kira:TerminusKira" \
  --model "$MODEL_NAME" \
  --env "$HARBOR_ENV" \
  --n-concurrent "$N_CONCURRENT" \
  "${task_selectors[@]}" \
  "${agent_kwargs[@]}"

# Auto-split tasks into passed/failed/errored-CF lists for downstream CF runs.
# Files land in jobs/<JOB_NAME>/tasks_*.txt
JOB_DIR="jobs/${JOB_NAME}"
SPLITTER="$(dirname "$0")/../scripts/split_job_tasks.sh"
if [[ -d "$JOB_DIR" && -x "$SPLITTER" ]]; then
    "$SPLITTER" "$JOB_DIR" || echo "[warn] split_job_tasks.sh failed (continuing)" >&2
else
    echo "[warn] skipping task split: JOB_DIR=$JOB_DIR  splitter=$SPLITTER" >&2
fi
