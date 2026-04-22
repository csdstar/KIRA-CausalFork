#!/usr/bin/env bash
set -euo pipefail

source /home/star/env/miniconda/etc/profile.d/conda.sh
conda activate kira

set -a
source /home/star/project/KIRA-CausalFork/.env
set +a

JOB_NAME="${BASE_JOB_PREFIX}-$(date +%Y-%m-%d__%H-%M)"

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

"$CONDA_PREFIX/bin/harbor" run \
  --dataset "$DATASET" \
  --n-tasks "$N_TASKS" \
  --job-name "$JOB_NAME" \
  --agent-import-path "terminus_kira.terminus_kira:TerminusKira" \
  --model "$MODEL_NAME" \
  --env "$HARBOR_ENV" \
  --n-concurrent "$N_CONCURRENT" \
  "${agent_kwargs[@]}"
