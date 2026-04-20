#!/usr/bin/env bash
set -euo pipefail

source /home/star/env/miniconda/etc/profile.d/conda.sh
conda activate kira

set -a
source /home/star/project/KIRA-CausalFork/.env
set +a
# KIRA_REASONING_* controls are exported from .env above.

JOB_NAME="${BASE_JOB_PREFIX}-$(date +%Y-%m-%d__%H-%M)"

agent_kwargs=()
if [[ -n "${MODEL_INFO:-}" ]]; then
  agent_kwargs+=(--agent-kwarg "model_info=${MODEL_INFO}")
fi

"$CONDA_PREFIX/bin/harbor" run \
  --dataset "$DATASET" \
  --n-tasks "$N_TASKS" \
  --job-name "$JOB_NAME" \
  --agent-import-path "terminus_kira.terminus_kira:TerminusKira" \
  --model "$MODEL_NAME" \
  --env "$HARBOR_ENV" \
  --n-concurrent "$N_CONCURRENT" \
  --exclude-task-name vuejs__core.d0b513eb.if.5999ec00 \
  "${agent_kwargs[@]}"
