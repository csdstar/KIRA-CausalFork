# KIRA-CausalFork Harness 组件讲解

## 1. 文档目标

这份文档以当前仓库为例，系统讲解一个 Terminal-Bench agent harness 在代码层面通常由哪些组件组成，以及这些组件分别承担什么职责、暴露什么输入输出接口、如何在整体执行链路中协作。

这里重点分析的是仓库内真正参与 harness 运行的部分，而不是整个仓库里的所有子项目。也就是说，本文聚焦：

- `terminus_kira/`
- `prompt-templates/`
- `anthropic_caching.py`
- `run-scripts/`
- `pyproject.toml`

不展开讲 `KIRA-Slack/` 和 `KiraClaw/`。

---

## 2. 先建立整体视图

### 2.1 这个仓库里的 harness 是什么

这个仓库里的核心 harness 是 `TerminusKira`。

它不是从零自带全部运行时，而是建立在外部 `harbor` 框架和 `Terminus2` agent 基座之上的一个扩展实现。`README.md` 也明确写了它是“built on Terminus 2”，见 [README.md](/home/star/project/KIRA-CausalFork/README.md:32)。

因此，代码结构可以分成两层：

1. 外部底座层：`harbor / Terminus2`
2. 仓库内自定义层：`TerminusKira` 及其相关组件

### 2.2 仓库内 harness 组件总表

从当前仓库看，主要组件有：

| 组件 | 文件 | 角色 |
|---|---|---|
| 包导出层 | `terminus_kira/__init__.py` | 暴露 agent 类 |
| 主 harness agent | `terminus_kira/terminus_kira.py` | 主循环、工具调用、命令执行、图片读取、完成确认 |
| 反事实扩展 agent | `terminus_kira/terminus_kira_cf.py` | 在命令执行前插入 counterfactual planning |
| 反事实规划器 | `terminus_kira/counterfactual_planner.py` | 生成候选 workflow、打分、选择最终计划 |
| reasoning 控制层 | `terminus_kira/reasoning_controls.py` | 面向不同 provider 组装 reasoning/thinking 请求参数 |
| prompt 缓存层 | `anthropic_caching.py` | 给最近消息打上 Anthropic ephemeral cache 控制 |
| 系统 prompt 模板 | `prompt-templates/terminus-kira.txt` | 定义 agent 看到的任务与环境描述格式 |
| 启动脚本 | `run-scripts/run-kira.sh`, `run-scripts/run-kira-cf.sh` | 把 conda、环境变量、job 参数和 Harbor 运行入口连起来 |
| 项目打包元信息 | `pyproject.toml` | 说明包名与依赖 |

### 2.3 一句话理解整体执行链

整体链路可以先压缩成下面这张图：

```text
run-scripts/*.sh
  -> harbor run
    -> 导入 agent 类
      -> TerminusKira / TerminusKiraCF
        -> 读取 prompt template
        -> 调用 LLM（native tool calling）
        -> 解析 tool calls
        -> 执行 commands 或 image_read
        -> 写回 observation / trajectory
        -> 继续下一轮，直到 task_complete 双确认结束
```

如果是反事实版本，则中间多一层：

```text
LLM -> commands -> CounterfactualPlanner -> 选择新 commands -> 执行
```

---

## 3. 外部底座：这个 harness 依赖哪些 Harbor 组件

虽然这些类不在本仓库内，但理解它们对读 harness 很重要，因为本仓库很多接口设计都是围绕这些基座类型来的。

在 [terminus_kira.py](/home/star/project/KIRA-CausalFork/terminus_kira/terminus_kira.py:31) 到 [55](/home/star/project/KIRA-CausalFork/terminus_kira/terminus_kira.py:55) 里可以看到主要依赖：

- `Terminus2`
- `Command`
- `TmuxSession`
- `BaseEnvironment`
- `AgentContext`
- `Chat`
- `LLMResponse`
- `UsageInfo`
- `Step / Observation / ObservationResult / ToolCall / Metrics`

这些基座类型的作用可以概括为：

- `Terminus2`：提供 agent 运行框架、会话、日志、summarization、trajectory 等基础能力
- `Command`：统一描述“要往 tmux 里发的一条命令”
- `TmuxSession`：与终端交互
- `BaseEnvironment`：环境执行层，例如容器中执行 `base64`
- `Chat`：保存消息历史、累计 tokens / cost
- `AgentContext`：任务级上下文与统计
- trajectory 相关模型：把每一轮 agent 行为记录为结构化步骤

所以本仓库里写 harness，不是直接从裸 shell 开始，而是“在 Harbor 的 agent 契约之上”实现。

---

## 4. 组件一：包导出层

文件： [terminus_kira/__init__.py](/home/star/project/KIRA-CausalFork/terminus_kira/__init__.py:1)

### 4.1 代码作用

这个文件很小，但在 Python 包设计里有两个职责：

1. 让外部可以通过 `terminus_kira` 这个包访问 agent 类
2. 指定包级公开接口

当前代码：

```python
from terminus_kira.terminus_kira import TerminusKira
from terminus_kira.terminus_kira_cf import TerminusKiraCF

__all__ = ["TerminusKira"]
```

### 4.2 输入输出接口

它没有函数式输入输出，而是提供“模块导出接口”。

- 输入：无
- 输出：包导出符号

### 4.3 设计含义

这里有一个值得讲给师兄听的点：

- 文件中确实 import 了 `TerminusKiraCF`
- 但 `__all__` 只保留了 `TerminusKira`

这意味着：

- 包内部知道 CF 版本存在
- 但包级公共导出仍更偏向 base 版本

这就是一个典型的“内部有多个实现，但公开接口仍主打默认版本”的包设计。

---

## 5. 组件二：系统 Prompt 模板

文件： [prompt-templates/terminus-kira.txt](/home/star/project/KIRA-CausalFork/prompt-templates/terminus-kira.txt:1)

### 5.1 组件职责

这个文件定义了主 agent 每轮交给模型的系统任务框架。

它告诉模型：

- 自己是在 Linux 终端里解决命令行任务的 agent
- 目标是通过“成批 shell commands”完成任务
- 不要依赖人类干预
- 在 `task_complete` 前要做最小变更检查

### 5.2 输入输出接口

模板中有两个占位符：

- `{instruction}`
- `{terminal_state}`

因此它的逻辑接口是：

- 输入：
  - 原始任务描述
  - 当前终端状态
- 输出：
  - 拼接后的 prompt 字符串

### 5.3 设计作用

这个组件非常重要，因为它是 harness 的“策略约束入口”。

很多 agent 行为虽然最后体现在 Python 代码里，但模型为什么知道自己要：

- 自主完成任务
- 稳妥收尾
- 不随意修改无关状态

其实首先是由这个 prompt 定义的。

也就是说，prompt 模板是 harness 的“软控制层”，而 Python 逻辑是“硬控制层”。

---

## 6. 组件三：Prompt 缓存层

文件： [anthropic_caching.py](/home/star/project/KIRA-CausalFork/anthropic_caching.py:7)

### 6.1 组件职责

`add_anthropic_caching(messages, model_name)` 的作用是：

- 当模型属于 Anthropic / Claude 家族时
- 对最近 3 条消息加上 `cache_control: {"type": "ephemeral"}`

这是一个横切优化组件，不负责决策，只负责降低延迟和成本。

### 6.2 输入输出接口

输入：

- `messages: List[Dict[str, Any] | Message]`
- `model_name: str`

输出：

- 加过 cache 标记的新 messages 列表

### 6.3 接口设计特点

这个函数有两个设计点很值得讲：

1. 它对消息做 `deepcopy`
   - 避免原地修改上游 chat history
2. 它同时兼容：
   - `dict` 风格消息
   - `Message` 风格对象

这是一种典型的“轻量型适配器组件”。

### 6.4 它插在哪

它被主 harness 用在两个地方：

- 工具调用主链路，见 [terminus_kira.py](/home/star/project/KIRA-CausalFork/terminus_kira/terminus_kira.py:729)
- 图片分析链路，见 [terminus_kira.py](/home/star/project/KIRA-CausalFork/terminus_kira/terminus_kira.py:677)

所以它并不拥有独立流程，只是作为“请求预处理器”被复用。

---

## 7. 组件四：Reasoning 控制层

文件： [terminus_kira/reasoning_controls.py](/home/star/project/KIRA-CausalFork/terminus_kira/reasoning_controls.py:1)

### 7.1 为什么需要它

不同模型提供商对“推理模式 / thinking 模式”的参数名字并不统一。

例如代码里区分了：

- `moonshot`
- `qwen`
- `minimax`
- `generic`

所以如果把 reasoning 参数逻辑硬写在主 harness 里，主链路会越来越乱。这个文件就是把这部分 provider 差异抽出去，形成一个独立组件。

### 7.2 提供了哪些接口

主要公开能力有 4 类：

#### `detect_provider_family(model_name)`

输入：

- `model_name`

输出：

- provider family 字符串：`moonshot / qwen / minimax / generic`

#### `read_reasoning_env()`

输入：

- 环境变量：
  - `KIRA_REASONING_MODE`
  - `KIRA_THINKING_BUDGET`
  - `KIRA_MINIMAX_REASONING_SPLIT`

输出：

- 结构化配置 dict

#### `build_reasoning_request_overrides(model_name, reasoning_effort, ...)`

输入：

- `model_name`
- `reasoning_effort`
- `include_reasoning_effort`

输出：

- 可直接并入 LiteLLM 请求参数的 dict

它可能返回的内容包括：

- `reasoning_effort`
- `extra_body["thinking"]`
- `extra_body["enable_thinking"]`
- `extra_body["thinking_budget"]`
- `extra_body["reasoning_split"]`

#### `apply_reasoning_temperature_rules(model_name, kwargs)`

输入：

- `model_name`
- 请求 kwargs

输出：

- 原地修改 `kwargs["temperature"]`

### 7.3 组件设计价值

这个组件的价值在于把“provider-specific API 参数兼容”从主业务流程剥离出来。

这类组件很像网络层中的 request middleware：

- 主 harness 只关心“我要发请求”
- reasoning control 层负责“怎么把不同 provider 的字段拼正确”

### 7.4 它插在哪

主 harness 的两个 LLM 请求入口都会调用它：

- `_call_llm_with_tools()`，见 [terminus_kira.py](/home/star/project/KIRA-CausalFork/terminus_kira/terminus_kira.py:741)
- `_call_llm_for_image()`，见 [terminus_kira.py](/home/star/project/KIRA-CausalFork/terminus_kira/terminus_kira.py:606)

反事实 planner 里也复用了同样的 reasoning 控制逻辑，见 [counterfactual_planner.py](/home/star/project/KIRA-CausalFork/terminus_kira/counterfactual_planner.py:10)。

---

## 8. 组件五：主 Harness Agent `TerminusKira`

文件： [terminus_kira/terminus_kira.py](/home/star/project/KIRA-CausalFork/terminus_kira/terminus_kira.py:220)

这是整个仓库里最核心的组件。

为了讲解清楚，可以把它再拆成若干子组件来看。

### 8.1 子组件 A：数据结构层

#### `ToolCallResponse`

定义位置： [terminus_kira.py](/home/star/project/KIRA-CausalFork/terminus_kira/terminus_kira.py:67)

职责：

- 封装一次 LiteLLM 返回结果

字段：

- `content`
- `tool_calls`
- `reasoning_content`
- `usage`

输入输出意义：

- 输入：模型原始 response 被解析后填充进去
- 输出：供 `_handle_llm_interaction()` 后续统一处理

#### `ImageReadRequest`

定义位置： [terminus_kira.py](/home/star/project/KIRA-CausalFork/terminus_kira/terminus_kira.py:77)

职责：

- 表示一次图片分析请求

字段：

- `file_path`
- `image_read_instruction`

它是把 tool schema 的 JSON 参数，转换成内部 Python 类型的最小数据模型。

### 8.2 子组件 B：Tool schema 层

定义位置： [terminus_kira.py](/home/star/project/KIRA-CausalFork/terminus_kira/terminus_kira.py:143)

这里定义了 3 个原生工具：

- `execute_commands`
- `task_complete`
- `image_read`

#### `execute_commands`

输入接口：

- `analysis: str`
- `plan: str`
- `commands: list[{keystrokes, duration}]`

输出接口：

- 没有直接返回值
- 它的结果会被解析成内部 `Command` 列表

#### `task_complete`

输入接口：

- 空对象

输出接口：

- 内部被解析为 `is_task_complete = True`

#### `image_read`

输入接口：

- `file_path`
- `image_read_instruction`

输出接口：

- 内部被解析为 `ImageReadRequest`

#### 设计意义

这个工具层非常关键，因为它定义了“模型和 harness 之间的正式协议”。

从架构角度看，它就是：

- LLM side API contract
- Harness side parser contract

一旦工具 schema 稳定，模型输出和本地执行层就能解耦。

### 8.3 子组件 C：会话与超时控制层

#### `_with_block_timeout(coro, timeout_sec)`

位置： [terminus_kira.py](/home/star/project/KIRA-CausalFork/terminus_kira/terminus_kira.py:233)

输入：

- 任意 coroutine
- 超时秒数

输出：

- 返回 coroutine 的结果
- 超时则抛 `BlockError`

作用：

- 防止基础设施调用长时间卡死

这是典型的 harness “保护层”组件。

### 8.4 子组件 D：命令执行器

#### `_execute_commands(commands, session)`

位置： [terminus_kira.py](/home/star/project/KIRA-CausalFork/terminus_kira/terminus_kira.py:240)

输入：

- `commands: list[Command]`
- `session: TmuxSession`

输出：

- `tuple[bool, str]`
- 当前实现返回 `(False, cleaned_terminal_output)`

#### 核心机制

这个函数做了 4 件事：

1. 用 `session.send_keys()` 往 tmux 发送命令
2. 每条命令后追加唯一 marker，例如 `__CMDEND__7__`
3. 用 `capture_pane()` 轮询 marker 是否出现
4. 汇总增量输出并滤掉 marker 行

#### 为什么这很像一个独立组件

因为它已经不是简单“for cmd in commands: run”。

它承担了：

- 交互式终端写入
- 提前结束检测
- 输出清洗
- 运行时间节省统计

这就是 harness 里常见的“execution adapter”。

### 8.5 子组件 E：LiteLLM 连接参数解析层

#### `_get_litellm_connection_kwargs()`

位置： [terminus_kira.py](/home/star/project/KIRA-CausalFork/terminus_kira/terminus_kira.py:365)

输入：

- `self._llm` 上可能存在的 `_api_base / _api_key`
- 若干环境变量：
  - `API_BASE`
  - `ANTHROPIC_API_BASE`
  - `ANTHROPIC_BASE_URL`
  - `API_KEY`
  - `MOONSHOT_API_KEY`
  - `ANTHROPIC_API_KEY`

输出：

- `{"api_base": ..., "api_key": ...}` 的子集

#### 设计意义

这是一层“连接参数抽象”：

- 主流程不需要每次都自己判断 API key 从哪来
- 只要统一 `kwargs.update(...)`

它属于 harness 的 provider integration 组件。

### 8.6 子组件 F：调试可观测性层

#### `_debug_print_litellm_connection(context, kwargs)`

位置： [terminus_kira.py](/home/star/project/KIRA-CausalFork/terminus_kira/terminus_kira.py:312)

输入：

- 当前上下文名，例如 `_call_llm_with_tools`
- LiteLLM 请求参数

输出：

- 无函数返回值
- 打印脱敏后的调试信息

作用：

- 用于排查模型连接、reasoning 是否开启、api_base/api_key 来源

这类组件是 harness 工程里经常被忽视但非常重要的“运维友好层”。

### 8.7 子组件 G：工具调用响应解析层

#### `_extract_tool_calls(response)`

输入：

- LiteLLM response

输出：

- 统一格式的 `list[dict]`

#### `_extract_usage_info(response)`

输入：

- LiteLLM response

输出：

- `UsageInfo | None`

#### `_parse_tool_calls(tool_calls)`

输入：

- `tool_calls: list[dict]`

输出：

- `tuple[list[Command], bool, str, str, str, ImageReadRequest | None]`

也就是：

- `commands`
- `is_task_complete`
- `feedback`
- `analysis`
- `plan`
- `image_read`

#### 设计意义

这是一层很标准的 “协议解析器”：

- 上游是 LLM 的工具调用协议
- 下游是 Python 执行层的内部对象

它把“外部协议”翻译成“内部执行计划”。

### 8.8 子组件 H：图片分析子链路

#### `_call_llm_for_image(messages, model, temperature, max_tokens)`

输入：

- 多模态消息
- 模型参数

输出：

- LiteLLM response

#### `_execute_image_read(image_read, chat, original_instruction)`

输入：

- `ImageReadRequest`
- `Chat`
- 原始任务文本

输出：

- `str`，格式化后的“图片读取结果”

#### 这条链路干了什么

1. 用 `environment.exec("base64 <file>")` 取出图片
2. 根据后缀推断 MIME type
3. 构造成 `text + image_url(data:base64...)` 的多模态消息
4. 调用 LiteLLM
5. 把结果记入 token 统计

#### 为什么它是单独组件

因为它和普通命令执行完全不同：

- 普通命令是“终端交互”
- 图片分析是“环境读文件 + 模型视觉理解”

因此这是一条典型的 side-path tool executor。

### 8.9 子组件 I：主 LLM 适配层

#### `_call_llm_with_tools(messages)`

位置： [terminus_kira.py](/home/star/project/KIRA-CausalFork/terminus_kira/terminus_kira.py:721)

输入：

- `messages`

输出：

- `ToolCallResponse`

#### 它的职责

1. 应用 Anthropic caching
2. 拼 `tools=TOOLS`
3. 拼 reasoning 参数
4. 拼 API 连接参数
5. 直接调用 `litellm.acompletion`
6. 提取内容、tool_calls、usage、reasoning_content
7. 处理上下文超限和输出截断语义

#### 为什么这是 harness 的核心适配器

因为这个函数承担了“把 Harbor 的 chat world 和 LiteLLM 的 provider world 接起来”的任务。

它是整个 harness 的模型调用中枢。

### 8.10 子组件 J：LLM 交互控制器

#### `_handle_llm_interaction(chat, prompt, logging_paths, original_instruction, session)`

位置： [terminus_kira.py](/home/star/project/KIRA-CausalFork/terminus_kira/terminus_kira.py:785)

输入：

- `Chat`
- 当前 prompt
- 日志路径
- 原始任务
- `TmuxSession`

输出：

- `tuple[commands, is_task_complete, feedback, analysis, plan, llm_response, image_read]`

#### 它是怎么工作的

1. 从 chat history 组消息
2. 调 `_call_llm_with_tools()`
3. 回写 assistant message 与 tool placeholder message
4. 更新 usage/cost
5. 处理：
   - `ContextLengthExceededError`
   - `OutputLengthExceededError`
6. 最终调用 `_parse_tool_calls()`

#### 这一层的架构角色

它是主循环和模型调用之间的“事务协调器”。

主循环不需要知道：

- tool_calls 怎么抽取
- usage 怎么累计
- 上下文超限怎么回退

因为这些都在这里完成。

### 8.11 子组件 K：主 Agent Loop

#### `_run_agent_loop(initial_prompt, chat, logging_dir, original_instruction)`

位置： [terminus_kira.py](/home/star/project/KIRA-CausalFork/terminus_kira/terminus_kira.py:988)

输入：

- 初始 prompt
- `Chat`
- 日志目录
- 原始任务

输出：

- `int`：实际执行 episode 数

#### 这是整个 harness 的“心脏”

每轮 loop 主要做这些事：

1. 检查 session 是否存活
2. 必要时 proactive summarization
3. 调 `_handle_llm_interaction()` 拿下一步动作
4. 根据是否是 `image_read` 分支执行不同工具
5. 处理 `task_complete` 双确认
6. 写 trajectory
7. 生成下一轮 prompt

#### 双分支结构

这个 loop 实际上包含两条执行分支：

1. `image_read` 分支
2. `commands` 分支

其中 `commands` 分支会调用 `_execute_commands()`，见 [terminus_kira.py](/home/star/project/KIRA-CausalFork/terminus_kira/terminus_kira.py:1221)。

#### 为什么这是典型 harness 设计

因为 harness 的真正任务不是“问一次模型”，而是：

- 维持一个多轮 agent-loop
- 组织 observation -> action -> observation 的闭环

这正是 `_run_agent_loop()` 在做的事。

### 8.12 子组件 L：完成态保护层

#### `_get_completion_confirmation_message(terminal_output)`

位置： [terminus_kira.py](/home/star/project/KIRA-CausalFork/terminus_kira/terminus_kira.py:433)

输入：

- 当前终端状态

输出：

- 一段要求再次确认 completion 的文本

#### 设计作用

它把 `task_complete` 从“一次性结束”变成“二次确认结束”。

这是 harness 非常典型的 safety / robustness 组件，目的是减少模型误判完成。

---

## 9. 组件六：反事实扩展 Agent `TerminusKiraCF`

文件： [terminus_kira/terminus_kira_cf.py](/home/star/project/KIRA-CausalFork/terminus_kira/terminus_kira_cf.py:11)

### 9.1 组件职责

`TerminusKiraCF` 不是重写整个 harness，而是在 `TerminusKira` 基础上加了一层“命令执行前的反事实规划”。

类注释已经明确说了插入点：

```text
LLM native tool call -> parse execute_commands -> CounterfactualPlanner
-> selected commands -> tmux execution
```

### 9.2 输入输出接口

它重写的方法只有一个：

#### `_handle_llm_interaction(...)`

输入：

- 与父类相同：`chat / prompt / logging_paths / original_instruction / session`

输出：

- 与父类完全一致的 tuple

也就是说，它遵守父类接口，不改变上层 loop 的调用方式。

### 9.3 它做了什么

流程如下：

1. 先调用 `super()._handle_llm_interaction(...)`
2. 得到原始 `commands / analysis / plan / image_read / is_task_complete`
3. 只在满足条件时触发 planner：
   - 有 session
   - 不是 image_read
   - 有 commands
   - 不是 task_complete
   - `should_trigger(...) == True`
4. 抓取当前 terminal state
5. 调用 `CounterfactualPlanner.select(...)`
6. 如果 planner 选了新计划，就替换原 `commands`
7. 把 planner 的摘要补到 `analysis / plan`
8. 如果 planner 出错，则 fail-open

### 9.4 设计意义

这是一个很干净的“继承式增强组件”：

- 不改主 loop
- 不改执行器
- 不改 tool schema
- 只改“LLM 产生命令后、命令执行前”的决策层

这属于非常典型的 harness 扩展写法。

---

## 10. 组件七：反事实规划器 `CounterfactualPlanner`

文件： [terminus_kira/counterfactual_planner.py](/home/star/project/KIRA-CausalFork/terminus_kira/counterfactual_planner.py:40)

这是一个非常适合拿来讲“如何把研究想法包装成 harness 插件”的例子。

### 10.1 组件职责

它负责：

1. 判断是否要介入
2. 生成 counterfactual workflows
3. 对 factual 与 counterfactual 方案统一打分
4. 选择最终方案
5. 把 planner 自己产生的 token/cost/request time 回传给主 agent

### 10.2 数据模型接口

#### `CandidatePlan`

输入输出语义：

- 输入：候选 workflow 的结构化表示
- 输出：可被评分器和选择器消费的候选对象

字段包括：

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

#### `PlannerResult`

字段：

- `selected`
- `candidates`
- `changed`
- `rationale`
- `usage`
- `api_request_times_ms`

这说明 planner 不只是返回“选了哪个计划”，还把自身资源消耗也一并回传。

### 10.3 主接口：`should_trigger(...)`

输入：

- `commands`
- `episode`
- `is_task_complete`

输出：

- `bool`

它决定 planner 是否介入。

当前规则：

- `off`：永不触发
- `always`：有命令就触发
- `risk`：首轮优先触发，后续只对高风险命令触发

这是 planner 的“门控接口”。

### 10.4 主接口：`select(...)`

输入：

- `original_instruction`
- `terminal_state`
- `current_prompt`
- `analysis`
- `plan`
- `original_commands`
- `episode`

输出：

- `PlannerResult`

#### 这是 planner 的核心接口

它内部依次做：

1. 初始化 usage / request time 统计
2. 建 factual baseline
3. 调 `_generate_counterfactual_candidates()`
4. 调 `_score_candidates()`
5. 调 `_apply_completion_guardrails()`
6. 选最高分计划
7. 返回结果

### 10.5 候选生成接口

#### `_generate_counterfactual_candidates(...)`

输入：

- 原始任务
- 当前终端状态
- prompt / analysis / plan
- 原始 commands

输出：

- `list[CandidatePlan]`

它通过一次额外 LLM 调用，请模型生成替代 workflow。

所以这其实是一个“小型内部 planner agent”。

### 10.6 打分接口

#### `_score_candidates(...)`

输入：

- 原始任务
- 当前终端状态
- 候选列表
- `context_text`

输出：

- 已填写 `success / cost / risk / info_gain / robustness / score` 的候选列表

打分公式是：

```text
score =
  success
  - lambda_cost * cost
  - mu_risk * risk
  + gamma_info * info_gain
  + eta_robust * robustness
```

这体现了 harness 研究里很常见的一种模式：

- 让模型给出计划
- 再让模型评价计划
- 最后由本地算法选计划

### 10.7 API 适配与统计接口

planner 不只是逻辑层，它还兼顾：

- provider-specific reasoning 参数
- api_base/api_key 透传
- usage 累计
- 请求耗时累计

因此它本身就是一个完整的小型“子 harness 组件”。

### 10.8 设计价值

如果你要给师兄讲“怎么在一个已有 harness 上做研究型增强”，这个文件是最典型的例子：

- 不改主框架
- 抽成独立 planner
- 用标准输入输出接口和主 agent 对接

---

## 11. 组件八：运行脚本

文件：

- [run-kira.sh](/home/star/project/KIRA-CausalFork/run-scripts/run-kira.sh:1)
- [run-kira-cf.sh](/home/star/project/KIRA-CausalFork/run-scripts/run-kira-cf.sh:1)

### 11.1 组件职责

这两个脚本是 harness 的“启动装配层”。

它们负责把：

- conda 环境
- `.env` 环境变量
- job name
- Harbor CLI 参数

组装成一个可执行实验入口。

### 11.2 输入输出接口

#### 输入

通过环境变量输入：

- `DATASET`
- `N_TASKS`
- `MODEL_NAME`
- `HARBOR_ENV`
- `N_CONCURRENT`
- `BASE_JOB_PREFIX` / `CF_JOB_PREFIX`
- `.env` 中的 API / reasoning 控制等配置

#### 输出

- 启动一个 Harbor job

严格说脚本不是“返回值接口”，而是一个 process launcher。

### 11.3 `run-kira.sh`

做的事情：

1. 激活 conda 环境 `kira`
2. `source .env`
3. 根据时间拼 `JOB_NAME`
4. 调用 Harbor：

```bash
"$CONDA_PREFIX/bin/harbor" run \
  --dataset "$DATASET" \
  --n-tasks "$N_TASKS" \
  --job-name "$JOB_NAME" \
  --agent-import-path "terminus_kira.terminus_kira:TerminusKira" \
  --model "$MODEL_NAME" \
  --env "$HARBOR_ENV" \
  --n-concurrent "$N_CONCURRENT"
```

### 11.4 `run-kira-cf.sh`

与 base 版相同，但 agent import path 改成：

- `terminus_kira.terminus_kira_cf:TerminusKiraCF`

同时加了一些任务 include / exclude 条件。

这说明脚本层不仅是启动器，也是“实验调度策略层”的一部分。

---

## 12. 组件九：项目元信息

文件： [pyproject.toml](/home/star/project/KIRA-CausalFork/pyproject.toml:1)

### 12.1 组件职责

这个文件不是运行时逻辑，但它决定了 harness 如何被 Python 识别和安装。

### 12.2 输入输出接口

它的接口是“包管理器 / 构建工具接口”，而不是运行时函数接口。

关键信息有：

- 包名：`terminus-kira`
- Python 版本：`>=3.12`
- 依赖：
  - `anthropic`
  - `harbor>=0.1.44`
  - `litellm`
  - `tenacity`

### 12.3 设计意义

这一层说明：

- harness 逻辑代码并不直接依赖某个单一 provider SDK
- 它通过 `litellm` 做统一调用
- 通过 `harbor` 接入 Terminal-Bench / Terminus2 生态

---

## 13. 把所有组件串起来：一次任务是怎么跑完的

现在把组件拼在一起看，就会更清楚：

### 13.1 Base 版执行链

1. `run-kira.sh` 激活环境并调用 `harbor run`
2. Harbor 根据 `--agent-import-path` 导入 `TerminusKira`
3. `TerminusKira.run()` 保存原始任务并交给父类框架
4. 父类框架使用 `prompt-templates/terminus-kira.txt` 组 prompt
5. `_run_agent_loop()` 启动 agent loop
6. `_handle_llm_interaction()` 调 `_call_llm_with_tools()`
7. `TOOLS` 定义的接口约束模型返回 tool calls
8. `_parse_tool_calls()` 把 tool calls 解析成内部动作
9. 若是命令：
   - `_execute_commands()` 通过 tmux 执行
10. 若是图片：
   - `_execute_image_read()` 读取并分析
11. 把 observation 写入 trajectory
12. 若模型双确认 `task_complete`，则结束

### 13.2 CF 版执行链

与上面完全相同，只是在步骤 8 和 9 之间插入：

1. `TerminusKiraCF._handle_llm_interaction()` 拿到原始 commands
2. `CounterfactualPlanner.should_trigger()` 决定是否介入
3. `CounterfactualPlanner.select()` 生成和打分候选 workflow
4. 若新计划更优，则替换 commands
5. 再交给 `_execute_commands()` 执行

---

## 14. 如果要向师兄强调“一个 harness 的写法套路”，这个仓库里最值得讲的 6 个模式

### 模式 1：基于外部底座做小而硬的增强

这里没有重写整个 Terminal-Bench agent runtime，而是建立在 `Terminus2` 之上，专注改最关键的几个点：

- LLM 输出协议
- 执行优化
- 完成保护
- 多模态支持

这是非常工程化的 harness 设计。

### 模式 2：把“模型协议”抽成 Tool schema

`TOOLS` 这层等于把 LLM 输出从脆弱的文本解析，提升成了结构化协议。

这使得：

- 解析更稳
- 扩展更清楚
- 组件边界更明确

### 模式 3：把 provider 差异单独封装

`reasoning_controls.py` 和连接参数解析层都体现了这一点：

- 主业务不掺太多 provider 判断
- 差异逻辑放到专门组件

### 模式 4：把 side-path 工具做成独立执行链

`image_read` 不是塞进命令执行器里，而是拆出一条独立的图片分析链路。

这让主 loop 更清晰，也更容易以后继续加别的 tool。

### 模式 5：把研究增强做成“可插拔决策层”

`CounterfactualPlanner` 是这类设计的最好例子。

它没有污染：

- 主 loop
- tool schema
- 命令执行器

只是插在一个非常精准的位置。

### 模式 6：把实验启动层也视为 harness 的一部分

很多人只看 Python 代码，但实际上：

- conda 激活
- `.env` 注入
- Harbor 参数
- job name
- include / exclude task

这些都决定了 harness 真正怎么运行。

---

## 15. 最后的总结

如果要用一句话概括这个仓库里的 harness 写法，可以这样说：

它采用了“外部 agent runtime + 仓库内组件化增强”的结构：用 `TerminusKira` 作为主 orchestrator，把工具协议、命令执行、多模态读取、reasoning 适配、缓存、完成确认、反事实规划、启动脚本等能力拆成边界清晰的组件，再通过稳定的输入输出接口把这些组件串成一个多轮 agent loop。

如果你接下来要继续往这个 harness 上加功能，最自然的扩展点通常有 4 个：

1. 在 `TOOLS` 里加新工具协议
2. 在 `_run_agent_loop()` 里加新的 side-path 执行分支
3. 在 `_handle_llm_interaction()` 前后插新的决策中间层
4. 在 `run-scripts/` 和 `.env` 里加新的实验控制参数

这四个点，基本就是这个仓库展示出来的 harness 组件化写法精髓。
