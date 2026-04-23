# CF 实验工具使用手册

本文档详细说明四个工具的使用方法：

1. [`run-scripts/run-kira-cf.sh`](../run-scripts/run-kira-cf.sh) — 跑 CF benchmark
2. [`scripts/filter_tasks.py`](../scripts/filter_tasks.py) — 从 job 里挑任务
3. [`scripts/compare_runs.py`](../scripts/compare_runs.py) — 对比两次 job
4. `TerminusKiraCF` 的 `cf_mode` 参数 — 控制消融

> 本文档假设你已经用过 `run-kira.sh`，并且 `.env` 文件已经配好 `MODEL_NAME / DATASET / HARBOR_ENV / N_TASKS / N_CONCURRENT / CF_JOB_PREFIX`。

---

## 0. 前置：CF_MODE 的 8 种预设

| CF_MODE | 含义 | 用途 |
|---|---|---|
| `off` | CF 完全禁用，退化为原生 KIRA | baseline，健全性检查 |
| `adaptive` | 完整 CF：adaptive trigger + prescreener + LLM scoring + info_gain + robustness | 默认推荐 |
| `adaptive_no_scorer` | 关掉 LLM scorer，只用启发式评分 | 验证 LLM scorer 的贡献 |
| `adaptive_no_prescreen` | 关掉启发式过滤，所有候选都送 LLM scorer | 验证 prescreener 是否过度删减 |
| `adaptive_no_robust` | 不计 robustness 项 | 验证 robustness 权重 |
| `adaptive_no_info` | 不计 info_gain 项 | 验证 info_gain 权重 |
| `always_light` | 强制 light 模式，不做 adaptive 判断 | 验证 adaptive 的必要性 |
| `always_full` | 强制 full 模式 | 最强 CF，看上限 |

这些预设在 [terminus_kira_cf.py](../terminus_kira/terminus_kira_cf.py) 的 `_CF_MODE_PRESETS` 字典里定义，可以自行加。

---

## 1. `run-scripts/run-kira-cf.sh`

### 1.1 最简单的跑法

```bash
./run-scripts/run-kira-cf.sh
```

等价于：`CF_MODE=adaptive` + 读 `.env` 里所有默认参数 + 不筛任务（跑数据集前 `N_TASKS` 个）。

JOB_NAME 自动生成为 `${CF_JOB_PREFIX}-adaptive-YYYY-MM-DD__HH-MM`，落在 `jobs/` 目录。

### 1.2 跑指定消融配置

```bash
CF_MODE=off               ./run-scripts/run-kira-cf.sh    # baseline
CF_MODE=adaptive_no_scorer ./run-scripts/run-kira-cf.sh
CF_MODE=always_full       ./run-scripts/run-kira-cf.sh
```

非法 CF_MODE 会在脚本开头就被拒绝：

```
ERROR: unknown CF_MODE=bogus
Valid: off | adaptive | adaptive_no_scorer | ...
```

### 1.3 指定跑哪些任务

**a) 跑一个任务**
```bash
INCLUDE_TASK=reshard-c4-data ./run-scripts/run-kira-cf.sh
```

**b) 跑一个任务文件里的所有任务**

`failed_tasks.txt` 内容举例（每行一个任务名，`#` 开头行被忽略）：
```
# base run 失败的任务
break-filter-js-from-html
largest-eigenval
password-recovery
write-compressor
```

```bash
INCLUDE_TASKS_FILE=failed_tasks.txt ./run-scripts/run-kira-cf.sh
```

⚠️ 注意：harbor 的 `--n-tasks` 仍然生效，等于"从匹配到的任务里再取前 N 个"。如果想跑完文件里所有任务，设 `N_TASKS` 足够大（或改 `.env`）。

**c) 排除某些任务**
```bash
EXCLUDE_TASKS_FILE=broken_tasks.txt ./run-scripts/run-kira-cf.sh
```

**d) 同时 include + exclude**（少见但支持）
```bash
INCLUDE_TASKS_FILE=candidates.txt EXCLUDE_TASKS_FILE=skip.txt \
    ./run-scripts/run-kira-cf.sh
```

### 1.4 临时覆盖 JOB_NAME

```bash
JOB_NAME=ablation-01-baseline CF_MODE=off ./run-scripts/run-kira-cf.sh
```

在做一轮消融对比时推荐手动命名，比如 `ablation-01-off` / `ablation-01-adaptive` / …，这样后面对比脚本看着整齐。

### 1.5 所有环境变量一览

| 变量 | 来源 | 默认 | 说明 |
|---|---|---|---|
| `CF_MODE` | shell | `adaptive` | 消融配置 |
| `INCLUDE_TASKS_FILE` | shell | — | 文件路径，每行一个 task name |
| `EXCLUDE_TASKS_FILE` | shell | — | 同上 |
| `INCLUDE_TASK` | shell | — | 单个 task name，和 FILE 叠加 |
| `JOB_NAME` | shell | `${CF_JOB_PREFIX}-${CF_MODE}-${DATE}` | 输出目录名 |
| `MODEL_NAME` | .env | 必填 | LLM 模型 |
| `DATASET` | .env | 必填 | harbor dataset |
| `HARBOR_ENV` | .env | 必填 | `docker` / `local` |
| `N_TASKS` | .env | 必填 | 最多跑多少任务 |
| `N_CONCURRENT` | .env | 必填 | 并发 |
| `MODEL_INFO / TEMPERATURE / MAX_TURNS / REASONING_EFFORT` | .env | 可选 | 透传给 agent |

---

## 2. `scripts/filter_tasks.py`

### 2.1 看一眼 job 整体状态

```bash
python scripts/filter_tasks.py jobs/CF-2026-04-16__12-17 --stats
```

stderr 输出：
```
=== status summary ===
  passed        4
  failed        6
  partial       0
  errored       3
  unfinished    0
======================
```

stdout 输出（默认是 `--status failed`）：每行一个 task name。

### 2.2 四种输出格式

| `--format` | 输出样式 | 用途 |
|---|---|---|
| `lines`（默认） | 每行一个 task name | 写入文件给 `INCLUDE_TASKS_FILE` |
| `csv` | 逗号分隔 | 肉眼快速浏览 |
| `harbor-include` | `--include-task-name foo --include-task-name bar …` | 直接 `xargs` 到 harbor |
| `harbor-exclude` | `--exclude-task-name foo …` | 同上 |

### 2.3 五种状态

| `--status` | 含义 | reward 条件 |
|---|---|---|
| `passed` | 通过 | reward ≥ 1.0 |
| `failed` | 失败 | reward ≤ 0.0 |
| `partial` | 部分通过 | 0 < reward < 1 |
| `errored` | 异常退出（CancelledError / AgentTimeoutError 等） | 在 `exception_stats` |
| `unfinished` | 目录存在但 `result.json` 里没记录 | 磁盘上有目录，json 里没 |
| `all` | 所有任务 | 合并以上 |

可以多个一起传：
```bash
python scripts/filter_tasks.py jobs/base-... --status failed errored partial
```

### 2.4 实战示例

**示例 1：提取 base 失败任务，给 CF 跑**
```bash
python scripts/filter_tasks.py jobs/base-qwen3.6-plus-polyglot-2026-04-21__11-51 \
    --status failed --stats > failed_tasks.txt

cat failed_tasks.txt | wc -l    # 看看挑出多少个
CF_MODE=adaptive INCLUDE_TASKS_FILE=failed_tasks.txt ./run-scripts/run-kira-cf.sh
```

**示例 2：跳过已通过的任务，只在未决的上跑 CF**

省钱技巧：如果 base 已经 100% 通过的任务，CF 再跑一次也不会变好（理论上），不如只跑没通过的。
```bash
python scripts/filter_tasks.py jobs/base-... \
    --status passed > already_solved.txt

EXCLUDE_TASKS_FILE=already_solved.txt CF_MODE=adaptive \
    ./run-scripts/run-kira-cf.sh
```

**示例 3：挑出 errored 任务重跑（可能是网络/docker 超时）**
```bash
python scripts/filter_tasks.py jobs/CF-... --status errored \
    --format lines > to_retry.txt

INCLUDE_TASKS_FILE=to_retry.txt CF_MODE=adaptive \
    JOB_NAME=retry-run ./run-scripts/run-kira-cf.sh
```

**示例 4：xargs 直接传参给 harbor（跳过临时文件）**
```bash
python scripts/filter_tasks.py jobs/base-... --status failed \
    --format harbor-include | \
xargs -o harbor run \
    --dataset "$DATASET" --n-tasks 50 \
    --agent-import-path terminus_kira.terminus_kira_cf:TerminusKiraCF \
    --agent-kwarg cf_mode=adaptive
```

### 2.5 注意事项

- **task_name vs trial_name**：harbor 目录名是 `task_name__RANDOMID`（trial_name），但 `--include-task-name` 要的是 `task_name`。本脚本自动剥后缀 `__XXXXXX`。
- **空输出会 exit 1**：如果 `--status failed` 但没有失败任务，脚本返回 1 并在 stderr 写 WARNING，shell 脚本里记得用 `|| true` 兜底。
- **同一 task 在 job 里有多个 trial**（harbor 重跑时），reward 按 result.json 里的为准。

---

## 3. `scripts/compare_runs.py`

### 3.1 最简单的跑法

```bash
python scripts/compare_runs.py \
    jobs/CF-off-2026-04-23__10-00 \
    jobs/CF-adaptive-2026-04-23__11-30
```

### 3.2 输出解读

脚本输出分三段：

**第一段：两个 job 的独立总结**
```
==== Job Summary ====
BASE: jobs/CF-off-2026-04-23__10-00
  trials=30  pass=12  fail=15  errored=3  pass_rate=40.0%
  main tokens: 7,130,647  cost: $1.0377

CF:   jobs/CF-adaptive-2026-04-23__11-30
  trials=30  pass=18  fail=10  errored=2  pass_rate=60.0%
  main tokens: 8,022,037  cost: $1.2242
  CF   tokens: 1,305,412  cost: $0.1831
  CF overhead ratio (cf/main): 16.3%           ← CF 模块额外开销占主任务比例
  CF trials with activity: 28/30                ← 30 个 trial 里 28 个触发过 CF
  CF episodes triggered: 104  changed_plans: 37 ← 104 次 CF 决策，改写了 37 次 factual
  CF planner_mode histogram: full=22, light=70, skip=12
```

**第二段：2×2 flip 表 + McNemar 检验**
```
==== Paired (common tasks) ====
n_common=30  n_compared=25  (errored tasks excluded from McNemar)
             CF pass  CF fail
  base pass      10        2
  base fail       8        5
flips_up (CF rescued):   8      ← base 失败 CF 成功（好）
flips_down (CF broke):   2      ← base 成功 CF 失败（坏）
net flip (up - down):   +6
McNemar exact two-sided p-value: 0.1094
→ not significant
```

**第三段：flip 任务清单（方便 case study）**
```
---- flips_up (CF rescued) ----
  + break-filter-js-from-html
  + largest-eigenval
  ...
---- flips_down (CF broke) ----
  - reshard-c4-data
```

### 3.3 参数

| 参数 | 作用 |
|---|---|
| `base_job_dir` | 第 1 个位置参数，通常是 baseline（如 CF_MODE=off） |
| `cf_job_dir` | 第 2 个位置参数，通常是实验组（如 CF_MODE=adaptive） |
| `-v, --verbose` | 强制列出所有 flip 任务（默认 flip 总数 ≤ 20 才列） |
| `--json out.json` | 把完整报告以 JSON 写到文件，方便后续 pandas 分析或画图 |

### 3.4 实战示例

**示例 1：做消融表**
```bash
for mode in off adaptive_no_scorer adaptive_no_prescreen always_full; do
    echo "=== adaptive vs $mode ==="
    python scripts/compare_runs.py \
        jobs/CF-${mode}-* jobs/CF-adaptive-* 2>&1 | grep -E "pass_rate|net flip|p-value|overhead"
    echo
done
```

**示例 2：导出 JSON 做可视化**
```bash
python scripts/compare_runs.py jobs/CF-off-* jobs/CF-adaptive-* \
    --json reports/cf_vs_baseline.json

# 然后用 jq 或 pandas 进一步加工
jq '.paired.flips_up' reports/cf_vs_baseline.json
```

**示例 3：批量对比多个配置**
```bash
mkdir -p reports
for exp in off adaptive adaptive_no_scorer adaptive_no_prescreen always_full; do
    python scripts/compare_runs.py \
        jobs/CF-off-* jobs/CF-${exp}-* \
        --json reports/${exp}_vs_off.json \
        > reports/${exp}_vs_off.txt 2>&1
done
```

### 3.5 对统计结果的解读

- **p<0.05 且 net flip > 0**：CF 有显著正收益
- **p<0.05 且 net flip < 0**：CF 有显著负收益（应该调参数或退回）
- **p≥0.05**：数据不足或差异不大，看 `net flip` 数值和 `flips_up`/`flips_down` 做定性判断
- **`n_common` 很小**：两个 job 跑的任务集不一致，几乎无法对比；先用 `filter_tasks.py` 对齐任务集
- **`errored` 很多**：检查 docker/网络，别把 CancelledError 当 CF 退化

### 3.6 CF overhead 的参考值

| overhead ratio | 解读 |
|---|---|
| < 10% | 省到家了，通常 `adaptive` + 多数任务走 `skip`/`light` |
| 10% - 30% | 可接受，重点看增益有没有超过开销 |
| 30% - 100% | 偏高，检查是不是 `always_full` 或 `early_accept_threshold` 太低 |
| > 100% | CF 花的比主模型还多，大概率有 bug 或参数有问题 |

---

## 4. 一个完整的消融流程（放在一起）

```bash
cd /home/star/project/KIRA-CausalFork

# ---- Step 1: 确定任务集 ----
# 从现有 base polyglot 实验里提取失败任务（作为“难任务”重点验证集）
python scripts/filter_tasks.py \
    jobs/base-qwen3.6-plus-polyglot-2026-04-21__11-51 \
    --status failed partial --stats > tasks/hard_set.txt

echo "任务数：$(wc -l < tasks/hard_set.txt)"

# ---- Step 2: 跑 5 种消融 ----
# 每种都用完全相同的任务集
for mode in off adaptive adaptive_no_scorer adaptive_no_prescreen always_full; do
    CF_MODE=$mode \
    INCLUDE_TASKS_FILE=tasks/hard_set.txt \
    JOB_NAME=abl01-${mode}-$(date +%m%d) \
        ./run-scripts/run-kira-cf.sh
done

# ---- Step 3: 两两对比 ----
mkdir -p reports/abl01
for mode in adaptive adaptive_no_scorer adaptive_no_prescreen always_full; do
    python scripts/compare_runs.py \
        jobs/abl01-off-* jobs/abl01-${mode}-* \
        --json reports/abl01/${mode}_vs_off.json \
        | tee reports/abl01/${mode}_vs_off.txt
done

# ---- Step 4: 浏览失败原因（可选，用 LLM 辅助分析） ----
harbor analyze jobs/abl01-adaptive-* --failing -m haiku \
    -o reports/abl01/adaptive_failure_analysis.json

# ---- Step 5: 可视化 trajectory（有界面需求时） ----
harbor view jobs/abl01-adaptive-*
```

---

## 5. 常见问题

**Q: `compare_runs.py` 报 `n_common=0`。**
两个 job 跑的任务集不一样。先 `ls jobs/A/ | sort > a.txt; ls jobs/B/ | sort > b.txt; diff a.txt b.txt` 看看。消融实验里永远用相同的 `INCLUDE_TASKS_FILE`。

**Q: CF tokens 都是 0。**
- 你跑的 `CF_MODE=off`：正常，off 模式不调 planner。
- 你跑的是旧代码的 job：`[KIRA CF STATS]` 行是 4-23 之后加的，旧 job 没这数据。
- `trial.log` 不存在或权限问题：`ls -la jobs/.../<trial>/trial.log` 检查。

**Q: `filter_tasks.py` 出来的任务 harbor 不认。**
harbor 的 `--include-task-name` 要的是**任务 dataset 里的名字**，不是目录名。本脚本剥 `__RANDOMID` 后缀一般就对了。如果还不对，用 `jq '.task_name' jobs/<trial>/result.json` 看真实名字。

**Q: `CF_MODE=off` 的 CF 和原 KIRA 跑出来不一样。**
`CF_MODE=off` 仍然经过 `TerminusKiraCF._handle_llm_interaction()`，但 planner 会在第一步就返回 factual，理论上等价于原 KIRA。如果结果差异大，检查：
- 同一 model、同一 temperature、同一 dataset commit
- 是不是 `_execute_commands` 的 failure streak 统计有副作用（应该没有，只读 output）

**Q: 想加新的消融维度。**
改 [`terminus_kira_cf.py`](../terminus_kira/terminus_kira_cf.py) 里的 `_CF_MODE_PRESETS` 字典，加一个 key；同时在 [`run-kira-cf.sh`](../run-scripts/run-kira-cf.sh) 的 `case "$CF_MODE"` 分支里加上该 key。
