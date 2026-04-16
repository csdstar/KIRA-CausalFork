#!/usr/bin/env bash
set -euo pipefail

source /home/star/env/miniconda/etc/profile.d/conda.sh
conda activate kira

set -a
source /home/star/project/KIRA-CausalFork/.env
set +a

"$CONDA_PREFIX/bin/harbor" run \
  --dataset "$DATASET" \
  --n-tasks "$N_TASKS" \
  --agent-import-path "terminus_kira.terminus_kira_cf:TerminusKiraCF" \
  --model "$MODEL_NAME" \
  --env "$HARBOR_ENV" \
  --n-concurrent "$N_CONCURRENT" \
  --exclude-task-name chess-best-move \
  --exclude-task-name sqlite-with-gcov \
  --exclude-task-name gpt2-codegolf \
  --exclude-task-name llm-inference-batching-scheduler
