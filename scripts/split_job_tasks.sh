#!/usr/bin/env bash
# Split a completed harbor job's tasks into three lists:
#   <job_dir>/tasks_passed.txt      — reward >= 1.0
#   <job_dir>/tasks_failed.txt      — reward < 1.0 (strict failures; verifier ran)
#   <job_dir>/tasks_errored_cf.txt  — errored AND exception class is plausibly
#                                     caused by agent's planning (worth CF retry)
#
# Exception classes considered "CF-worthy" (Class A):
#   AgentTimeoutError            — agent thrashed until timeout
#   ContextLengthExceededError   — agent filled context with noise
#   OutputLengthExceededError    — agent produced pathologically long output
#
# NOT included (verifier / infra / code bugs):
#   AttributeError, BadRequestError, CancelledError,
#   EnvironmentStartTimeoutError, AgentSetupTimeoutError, VerifierTimeoutError,
#   RewardFileNotFoundError, RewardFileEmptyError, NonZeroAgentExitCodeError, ...
#
# Usage:
#   scripts/split_job_tasks.sh <job_dir>
#
# Intended to be called automatically at the end of run-kira.sh, but can be
# run standalone on any finished job directory.

set -euo pipefail

if [[ $# -lt 1 ]]; then
    echo "usage: $0 <job_dir>" >&2
    exit 2
fi

JOB_DIR="$1"
if [[ ! -d "$JOB_DIR" || ! -f "$JOB_DIR/result.json" ]]; then
    echo "ERROR: $JOB_DIR is not a finished harbor job directory" >&2
    exit 2
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
FILTER="$SCRIPT_DIR/filter_tasks.py"

# CF-worthy exception classes (loop-appendable).
CF_EXCEPTIONS=(
    "AgentTimeoutError"
    "ContextLengthExceededError"
    "OutputLengthExceededError"
)

echo "=== splitting tasks of $JOB_DIR ==="

# passed
python "$FILTER" "$JOB_DIR" --status passed --stats \
    > "$JOB_DIR/tasks_passed.txt" 2>"$JOB_DIR/.filter_passed.err" || true
n_passed=$(wc -l < "$JOB_DIR/tasks_passed.txt" || echo 0)
cat "$JOB_DIR/.filter_passed.err" >&2 || true
rm -f "$JOB_DIR/.filter_passed.err"

# failed (verifier rejected)
python "$FILTER" "$JOB_DIR" --status failed partial \
    > "$JOB_DIR/tasks_failed.txt" 2>/dev/null || true
n_failed=$(wc -l < "$JOB_DIR/tasks_failed.txt" || echo 0)

# errored, CF-worthy subset only
# Run filter_tasks.py once per CF-worthy exception type, merge results.
tmp_errored=$(mktemp)
for exc in "${CF_EXCEPTIONS[@]}"; do
    python "$FILTER" "$JOB_DIR" --status errored --exception-type "$exc" \
        >> "$tmp_errored" 2>/dev/null || true
done
# dedupe and keep only non-empty lines
sort -u "$tmp_errored" | grep -v '^$' > "$JOB_DIR/tasks_errored_cf.txt" || true
rm -f "$tmp_errored"
n_err_cf=$(wc -l < "$JOB_DIR/tasks_errored_cf.txt" || echo 0)

echo "  → tasks_passed.txt       : $n_passed tasks"
echo "  → tasks_failed.txt       : $n_failed tasks"
echo "  → tasks_errored_cf.txt   : $n_err_cf tasks   (classes: ${CF_EXCEPTIONS[*]})"
echo
echo "To rerun failed tasks under CF:"
echo "    CF_MODE=adaptive INCLUDE_TASKS_FILE=$JOB_DIR/tasks_failed.txt \\"
echo "        ./run-scripts/run-kira-cf.sh"
