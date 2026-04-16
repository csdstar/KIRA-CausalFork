#!/usr/bin/env bash
set -euo pipefail

source /home/star/env/miniconda/etc/profile.d/conda.sh
conda activate kira

set -a
source /home/star/project/KIRA-CausalFork/.env
set +a
# KIRA_REASONING_* controls are exported from .env above.

JOB_NAME="${CF_JOB_PREFIX}-$(date +%Y-%m-%d__%H-%M)"

"$CONDA_PREFIX/bin/harbor" run \
  --dataset "$DATASET" \
  --n-tasks "$N_TASKS" \
  --job-name "$JOB_NAME" \
  --agent-import-path "terminus_kira.terminus_kira_cf:TerminusKiraCF" \
  --model "$MODEL_NAME" \
  --env "$HARBOR_ENV" \
  --n-concurrent "$N_CONCURRENT" \
  --exclude-task-name chess-best-move \
  --exclude-task-name sqlite-with-gcov \
  --exclude-task-name gpt2-codegolf \
  --exclude-task-name llm-inference-batching-scheduler
