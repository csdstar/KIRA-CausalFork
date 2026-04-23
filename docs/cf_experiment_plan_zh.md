# 反事实模块实验流程（精简版）

> 目标：在**计算资源紧缺**的前提下，最小代价地证明 `TerminusKiraCF` 相对 `TerminusKira` 的有效性，并定位每个 CF 子组件的贡献。

---

## 核心原则

1. **配对优先于独立采样**：在同一批任务上跑 base 和 CF，用 paired test 分析 flip。
2. **消融优先于规模**：5 种配置 × 30 任务 比 1 种配置 × 150 任务信息量大得多。
3. **先终端 benchmark，不追求跨数据集泛化**：CF 的候选模板（inspect-first / test-first / minimal-fix）本就假设终端 workflow。

---

## 三阶段实验路线

### 阶段 1 — 可行性验证（~30 trial，<1 天）

目的：确认 CF 的**额外 token 开销可接受**，且**至少救回一些 base 失败任务**。

步骤：
1. 在已有 `base-qwen3.6-plus-polyglot` 结果里，用 `scripts/filter_tasks.py` 提取 **所有 failing + half-passing 任务**
2. 用 `--include-task-name` 限定这批任务，跑 `CF_MODE=adaptive`
3. 用 `scripts/compare_runs.py` 看：
   - `changed_plan_rate`（CF 替换 factual 的比例）
   - `cf_token_overhead`（CF tokens / main tokens）
   - `flips_up` / `flips_down`

**Go/No-go 门槛**（建议）：
- `cf_token_overhead < 30%`
- `flips_up ≥ 3 且 flips_up > flips_down`

满足则进入阶段 2，否则回去调 `CFPlannerConfig`（先调 `early_accept_threshold` 和 `max_candidates_full`）。

### 阶段 2 — 配对消融（~150 trial）

用同一组 30 道难度混合的任务，跑 5 个配置。每个配置对应 `CF_MODE` 一种值：

| 配置 | CF_MODE | 验证 |
|---|---|---|
| A. baseline | `off` | 等价于原 KIRA，健全性检查 |
| B. 全开 | `adaptive` | 完整 CF 增益 |
| C. 无 LLM 评分 | `adaptive_no_scorer` | 启发式 vs LLM 评分价值 |
| D. 无 prescreener | `adaptive_no_prescreen` | 启发式过滤是否过度删减 |
| E. 总是 full | `always_full` | adaptive trigger 的必要性 |

每次实验用**相同任务集**（关键！）。跑完后 `compare_runs.py` 对比每个配置 vs A。

### 阶段 3 — 跨集泛化（~80 trial，可选）

拿阶段 2 胜出的一个配置，在 terminal-bench 上跑一次，证明不是 polyglot 过拟合。

---

## 关键指标

| 指标 | 来源 |
|---|---|
| `reward` 通过率 | `job_dir/result.json` → `stats.evals.*.reward_stats` |
| 主任务 token 总量 | 每个 trial 的 `agent/trajectory.json.final_metrics` |
| CF 模块 token 总量 | `trial.log` 中 `[KIRA CF STATS]` 行 |
| CF 候选细节 | `agent/episode-N/response_cf.json` sidecar |
| 失败原因 | `harbor analyze --failing` |

---

## 常用命令速查

```bash
# 1) 从已完成 job 中筛出失败/通过任务名
python scripts/filter_tasks.py jobs/base-qwen3.6-plus-polyglot-2026-04-21__11-51 \
    --status failed --format harbor-include > failed_tasks.txt

# 2) 用筛出的任务跑 CF 消融
CF_MODE=adaptive INCLUDE_TASKS_FILE=failed_tasks.txt ./run-scripts/run-kira-cf.sh

# 3) 对比两次 job
python scripts/compare_runs.py \
    jobs/base-qwen3.6-plus-polyglot-2026-04-21__11-51 \
    jobs/CF-adaptive-2026-04-23__xx-xx

# 4) 可视化任一 job
harbor view jobs/CF-adaptive-2026-04-23__xx-xx

# 5) LLM 帮你分析失败原因
harbor analyze jobs/CF-adaptive-... --failing -m haiku -o cf_failures.json
```

---

## 统计显著性

配对实验下用 **McNemar's test** 检测 flip 是否显著（`compare_runs.py` 已自动计算）。

经验阈值：
- n_common ≥ 20 且 flips_up - flips_down ≥ 5 → 通常 p < 0.05
- n_common < 10 → 报告 flip 案例研究，不声称统计显著

---

## 复现实验的注意事项

1. **固定 temperature 和 model**：`.env` 中 `TEMPERATURE` 和 `MODEL_NAME` 必须跨实验一致
2. **固定 max_turns**：不同 `MAX_TURNS` 会让对比失效
3. **同一 harbor 版本**：dataset 和 verifier 的升级都会改变 reward，跨版本不可比
4. **保留 `.env` 快照**：每个 job_dir 下 `config.json` 已经记录，但建议额外 `cp .env job_dir/env.snapshot`
