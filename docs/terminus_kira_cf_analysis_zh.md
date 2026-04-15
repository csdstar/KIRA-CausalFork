# Terminus-KIRA 反事实规划模块说明

## 1. 先说结论

你这次加的“因果 / 反事实”相关逻辑，核心由两个文件组成：

- `terminus_kira/counterfactual_planner.py`
- `terminus_kira/terminus_kira_cf.py`

它不是替换整个 agent 主循环，而是在 **LLM 已经给出 `execute_commands` 命令之后、tmux 真正执行这些命令之前**，额外插入一层“反事实候选计划生成与筛选”。

可以把它理解为：

```text
原始 KIRA:
LLM -> tool_calls -> parse commands -> execute_commands -> tmux

现在的 CF 版本:
LLM -> tool_calls -> parse commands -> CounterfactualPlanner -> 选中的 commands -> tmux
```

这个接入点在 `terminus_kira/terminus_kira_cf.py:15-18` 的类注释里也写出来了。

---

## 2. 这几个文件分别干什么

### `terminus_kira/counterfactual_planner.py`

这是反事实模块的主体，负责：

1. 判断当前这轮是否值得触发反事实规划
2. 基于原始计划生成若干替代 workflow
3. 对 factual plan 和 counterfactual plans 统一打分
4. 选出一个最终要执行的 commands 列表

### `terminus_kira/terminus_kira_cf.py`

这是一个新 agent 类 `TerminusKiraCF`，继承自原来的 `TerminusKira`。

它没有重写整个 `run()` 或 `_run_agent_loop()`，而是只重写了：

- `_handle_llm_interaction()`

也就是说，它复用了原有 KIRA 的大部分能力，只在“LLM 返回动作计划之后”插了一层反事实决策。

### `terminus_kira/__init__.py`

这里确实 import 了：

- `TerminusKira`
- `TerminusKiraCF`

但 `__all__` 还是只保留了：

```python
__all__ = ["TerminusKira"]
```

所以从包导出角度看，`TerminusKiraCF` 目前并没有被正式暴露成默认公共导出对象。

---

## 3. 模块插入到了哪个流程

### 原始流程

在原版 `TerminusKira` 里，关键链路是：

1. `_handle_llm_interaction()` 调 LLM
2. LLM 通过 native tool calling 返回 `execute_commands` / `task_complete` / `image_read`
3. `_parse_tool_calls()` 把 tool calls 解析成：
   - `commands`
   - `is_task_complete`
   - `analysis`
   - `plan`
   - `image_read`
4. `_run_agent_loop()` 拿到这些结果
5. 如果是命令分支，就直接 `_execute_commands(commands, self._session)`

对应代码位置：

- `terminus_kira/terminus_kira.py:660-855`
- `terminus_kira/terminus_kira.py:905-915`
- `terminus_kira/terminus_kira.py:1089-1096`

### 反事实模块插入后的流程

`TerminusKiraCF._handle_llm_interaction()` 先调用：

```python
await super()._handle_llm_interaction(...)
```

先拿到原本 KIRA 已经解析好的：

- `commands`
- `is_task_complete`
- `feedback`
- `analysis`
- `plan`
- `llm_response`
- `image_read`

然后它只在满足条件时对 `commands` 做二次处理。

所以它插入的位置非常明确：

```text
_run_agent_loop()
  -> _handle_llm_interaction()
     -> super()._handle_llm_interaction()   # 原始 KIRA 先产出 commands
     -> CounterfactualPlanner.select()      # 这里做反事实改写
  -> _execute_commands()                    # 执行改写后的 commands
```

也就是说，这层改造属于 **command execution 前的决策增强层**，而不是：

- prompt 层改造
- tool schema 改造
- tmux 执行器改造
- trajectory 存储层改造

---

## 4. `TerminusKiraCF` 的具体逻辑

代码在 `terminus_kira/terminus_kira_cf.py`。

### 4.1 类身份

它定义了一个新类：

- `TerminusKiraCF(TerminusKira)`，见 `:11`

并改了两个元信息：

- `name()` 返回 `terminus-kira-cf`，见 `:20-23`
- `version()` 返回 `1.0.0-cfplanner`，见 `:24-25`

这说明它想作为一个独立 agent 变体存在，而不是直接覆盖原版 `TerminusKira`。

### 4.2 初始化时做了什么

在 `__init__()` 中，它做了这些事：

1. 先调用 `super().__init__()`
2. 从 `self._llm` 里尝试取 `api_base`
3. 初始化 `CounterfactualPlanner`

初始化参数是：

- `model_name=self._model_name`
- `temperature=0.2`
- `max_candidates=4`
- `lambda_cost=0.10`
- `mu_risk=0.35`
- `gamma_info=0.15`
- `eta_robust=0.25`
- `api_base=api_base`
- `trigger_mode="risk"`

含义大致是：

- 最多比较 4 条路线
- 评分时对 `risk` 惩罚最大
- `cost` 惩罚较轻
- `info_gain` 和 `robustness` 有正向加成
- 默认不是每轮都触发，而是“风险触发”

---

## 5. 什么时候会触发反事实规划

入口判断在 `TerminusKiraCF._handle_llm_interaction()` 的这段：

- `session is not None`
- `image_read is None`
- `commands` 非空
- `not is_task_complete`
- `self._cf_planner.should_trigger(...)` 为真

见 `terminus_kira/terminus_kira_cf.py:78-90`。

也就是说，下面几种情况它不会介入：

- 当前没有命令要执行
- 当前是 `image_read` 路径
- 当前已经在走 `task_complete`
- 当前没有 session
- `should_trigger()` 判定不值得介入

这点很关键，因为它说明这个模块**只干预“普通 shell 命令执行分支”**。

### `should_trigger()` 的规则

定义在 `counterfactual_planner.py:62-97`。

它支持三种模式：

- `"off"`：永不触发
- `"always"`：只要有命令就触发
- `"risk"`：按当前规则判断

你当前初始化使用的是：

- `trigger_mode="risk"`

在这个模式下：

1. 第 1 轮或第 2 轮早期关键决策会优先触发
   - 代码是 `episode <= 1`
2. 后续轮次只在命令里出现高风险模式时触发，例如：
   - `rm -rf`
   - `sudo`
   - `apt install`
   - `pip install`
   - `sed -i`
   - `mv`
   - 重定向写文件 `>`
   - `git`
   - `make`
   - `pytest`
   - `python *.py`

这里的思想不是严格“因果推断”，更像是 **高风险动作前的反事实工作流重规划器**。

---

## 6. 触发之后它到底做了什么

### 6.1 先读取当前终端状态

`TerminusKiraCF` 会先抓当前 pane：

```python
terminal_state = await self._with_block_timeout(
    session.capture_pane(capture_entire=False)
)
```

见 `terminus_kira/terminus_kira_cf.py:91-94`。

这一步是为了把“当前屏幕上的上下文”交给 planner。

### 6.2 再调用 planner 做选择

调用入口是：

```python
result = await self._cf_planner.select(...)
```

传入的信息包括：

- `original_instruction`
- `terminal_state`
- `current_prompt`
- `analysis`
- `plan`
- `original_commands`
- `episode`

见 `terminus_kira/terminus_kira_cf.py:96-104`。

这意味着 planner 拿到的上下文比单纯 commands 丰富得多，它不仅看命令本身，也看：

- 原始任务
- 当前终端状态
- 模型当轮分析和计划

---

## 7. `CounterfactualPlanner` 的内部工作流程

### 7.1 数据结构

它定义了两个 dataclass：

#### `CandidatePlan`

表示一个候选工作流，包含：

- `name`
- `rationale`
- `commands`
- `expected_observation`
- `success`
- `cost`
- `risk`
- `info_gain`
- `robustness`
- `score`

见 `counterfactual_planner.py:10-22`。

#### `PlannerResult`

表示最终选择结果，包含：

- `selected`
- `candidates`
- `changed`
- `rationale`

见 `:24-29`。

### 7.2 第一步：建立 factual plan

`select()` 首先把模型原本给出的计划封装成一个 factual 候选：

- 名字是 `factual_model_plan`
- `commands=original_commands`
- `rationale=plan or analysis`

见 `:98-115`。

也就是说，原始计划不会丢，而是作为候选集里的 baseline。

### 7.3 第二步：生成 counterfactual candidates

`_generate_counterfactual_candidates()` 会单独再调用一次 LLM，请它基于当前上下文提出替代 workflow。

prompt 要求它：

- 从 factual commands 出发
- 生成最多 `max_candidates - 1` 条替代路线
- 倾向考虑这些类型：
  - inspect-first
  - test-first
  - minimal-fix
  - verification-first
  - multimodal-first
- 只返回严格 JSON

见 `counterfactual_planner.py:158-236`。

生成完成后，它会把 JSON 里的 commands 转回 `Command` 对象。

所以这一步本质是在做：

```text
原模型计划 -> 再让模型想几个“如果换个流程会不会更好”的备选方案
```

### 7.4 第三步：统一打分

`_score_candidates()` 会把 factual 和 counterfactual 候选一起发给 LLM 再评估一遍。

打分维度有 5 个：

- `success`
- `cost`
- `risk`
- `info_gain`
- `robustness`

见 `counterfactual_planner.py:238-313`。

最后综合分数公式是：

```text
score =
  success
  - lambda_cost * cost
  - mu_risk * risk
  + gamma_info * info_gain
  + eta_robust * robustness
```

在你的参数下，相当于：

```text
score =
  success
  - 0.10 * cost
  - 0.35 * risk
  + 0.15 * info_gain
  + 0.25 * robustness
```

可以看出：

- 成功率最重要
- 风险惩罚较重
- 鲁棒性和信息增益有奖励
- 时间/额外成本惩罚较轻

### 7.5 第四步：选最终路线

`select()` 用：

```python
selected = max(scored, key=lambda c: c.score)
```

选出最高分计划，见 `:128-145`。

如果选出来的不是 `factual_model_plan`，就认为：

- `changed = True`

这时 `TerminusKiraCF` 会真的把原始 `commands` 替换成新路线。

---

## 8. 替换发生在哪里

真正执行替换的是这几行：

```python
if result.changed:
    commands = result.selected.commands
```

见 `terminus_kira/terminus_kira_cf.py:106-107`。

这说明它的行为不是“只做分析记录”，而是会真实改写即将执行的命令序列。

后面 `_run_agent_loop()` 再调用 `_execute_commands(commands, self._session)` 时，执行到的已经是改写后的版本。

所以从执行语义上说，这个模块是一个 **pre-execution command selector**。

---

## 9. 它如何把结果写回当前 agent 流程

改写完命令之后，它还会更新返回给上游 loop 的：

- `analysis`
- `plan`

具体做法是：

```python
analysis = f"{analysis}\n\n[CounterfactualPlanner]\n{cf_summary}"
plan = f"{plan}\n\n[Selected counterfactual workflow]\n{result.selected.rationale}"
```

见 `terminus_kira/terminus_kira_cf.py:109-114`。

这带来两个效果：

1. 后续 `_run_agent_loop()` 记录 trajectory 时，message_content 会包含反事实规划摘要
2. 下一轮 prompt 基于 observation 继续推进时，日志里能看到这一层决策说明

不过要注意，这里改的是返回给 loop 的 `analysis/plan`，不是改 `llm_response.content` 本身。

---

## 10. 失败时怎么处理

### `CounterfactualPlanner.select()` 内部

如果生成候选或打分时出错，它会：

- 保留 factual plan
- `changed=False`
- 返回 fail-open 结果

见 `counterfactual_planner.py:148-156`。

### `TerminusKiraCF._handle_llm_interaction()` 外层

如果整个 CF planner 过程异常，它也不会中断 KIRA 原流程，而是：

- 不改命令
- 把错误以注释形式补进 `analysis`

见 `terminus_kira/terminus_kira_cf.py:116-120`。

所以这套设计是明显的 **fail open** 策略：

- planner 失败时，不阻塞 agent
- 回退到原始 KIRA 行为

这个设计对 harness 来说是合理的，因为它避免了“反事实模块把原 agent 用坏”。

---

## 11. 这个模块没有改动的部分

它没有修改下面这些核心机制：

- native tool definitions
- LLM tools 调用格式
- `_parse_tool_calls()`
- `_execute_commands()` 的 tmux/marker 执行器
- `image_read` 路径
- `task_complete` 双确认流程
- 主 `_run_agent_loop()` 骨架

所以它不是重写 agent，而是对原有 KIRA 的普通命令执行路径做增强。

---

## 12. 当前接线是否完整

从代码逻辑上看，**模块本身接线是通的**：

- 有独立 planner
- 有独立 agent 子类
- 有实际 hook 点
- 会真的改写 commands

但从“项目可用性”角度，还存在两个很实际的接线问题。

### 问题 1：默认导出不完整

`terminus_kira/__init__.py` 虽然 import 了 `TerminusKiraCF`，但：

```python
__all__ = ["TerminusKira"]
```

这表示它没有把 `TerminusKiraCF` 当成正式公开导出的一部分。

这不一定会阻止你用完整路径导入：

```python
terminus_kira.terminus_kira_cf:TerminusKiraCF
```

但如果有人希望从包级接口拿到它，就会比较别扭。

### 问题 2：当前仓库里没有看到运行入口切到 CF 版本

我在仓库里检索到：

- `TerminusKiraCF` 只在 `__init__.py` 和 `terminus_kira_cf.py` 内出现
- 现有 `run-scripts/*.sh` 和 README 仍然是指向原版 `TerminusKira`

这意味着：

- **代码已经接进包里了**
- 但 **默认运行脚本还没有切到这个新 agent**

也就是说，如果你没有显式把运行入口改成：

```bash
--agent-import-path "terminus_kira.terminus_kira_cf:TerminusKiraCF"
```

那这套反事实模块其实不会生效。

---

## 13. 从逻辑上看，它“都干了什么”

把整个模块压缩成一句话：

它在原始 KIRA 模型已经提出命令计划之后，拦截这批命令，结合任务目标、当前终端状态、模型自己的分析与计划，再额外生成几个“如果换条路线做会不会更好”的候选 workflow，按成功率、风险、成本、信息增益和鲁棒性统一打分，然后选一个计划交给原来的 tmux 执行器真正执行。

如果拆得更细，就是下面这 6 步：

1. 拿到原始 factual commands
2. 判断这轮是否需要反事实介入
3. 抓取当前终端状态
4. 生成 counterfactual 候选路线
5. 对 factual / counterfactual 统一评分
6. 选择最优路线并替换原 commands

---

## 14. 最终判断

最终判断是：

- 这套代码已经把“反事实规划器”作为一个 **执行前决策层** 接进了 `TerminusKira`
- 它插入的位置是 **`_handle_llm_interaction()` 返回命令之后、`_execute_commands()` 真正执行之前**
- 它当前影响的只有 **普通 shell commands 分支**
- 它不会影响：
  - `image_read`
  - `task_complete`
  - 底层 tmux 执行器
- 它采用的是 **LLM 生成替代方案 + LLM 再评分** 的反事实 workflow 选择方式
- 但要真正跑起来，你还需要确认运行入口是否已经切到 `TerminusKiraCF`

---

## 15. 我认为最值得你马上注意的两个点

1. `__init__.py` 里 `__all__` 没把 `TerminusKiraCF` 暴露出去，这属于导出不完整。
2. 仓库现有运行脚本还没看到改成 `TerminusKiraCF`，如果入口没切，这个模块等于还没真正上线。

