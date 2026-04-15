# Terminus-KIRA Harness 代码说明

## 1. 先说结论：`terminus_kira.py` 不是“完整自包含”的 harness 框架

它是这个仓库里最核心的 agent 实现文件，但不是完整 harness 的全部。

原因很直接：

- `TerminusKira` 是继承 `harbor.agents.terminus_2.Terminus2` 的子类，而不是从零实现的独立框架，见 `terminus_kira/terminus_kira.py:214`。
- 运行入口不是直接执行这个文件，而是通过 `harbor run` 动态加载它，见 `run-scripts/run_runloop.sh:14-19` 与 `README.md:87-95`。
- 项目依赖明确要求 `harbor>=0.1.44`，见 `pyproject.toml:7-12`。
- prompt 模板不在这个文件里，而在 `prompt-templates/terminus-kira.txt`。
- Anthropic prompt caching 也拆在 `anthropic_caching.py`。

所以更准确地说：

`terminus_kira/terminus_kira.py` 是这个仓库中“Terminus-KIRA harness 的核心自定义实现”，但完整运行时还依赖：

- 外部 `harbor / Terminus2` 基座
- `prompt-templates/terminus-kira.txt`
- `anthropic_caching.py`
- `run-scripts/*.sh` 中的启动方式

## 2. 它和 `KIRA-Slack`、`KiraClaw` 的关系

按当前仓库代码可见范围，`terminus_kira` 包没有直接 import 或调用 `KIRA-Slack`、`KiraClaw` 的运行时代码。

当前 `terminus_kira.py` 的外部依赖主要是：

- `harbor.*`
- `litellm`
- `tenacity`
- 本仓库内的 `anthropic_caching.py`
- 本仓库内的 `prompt-templates/terminus-kira.txt`

因此，这份说明只聚焦 `terminus_kira` 这套 harness 本身，不展开 `KIRA-Slack` 与 `KiraClaw`。

## 3. 这个文件到底负责什么

如果把 `harbor.Terminus2` 看成“通用终端 agent 框架底座”，那 `TerminusKira` 负责的是上层策略替换和增强，主要包括：

1. 把 Terminus2 原本偏 ICL/解析式的输出方式，改成原生 `tools` 调用。
2. 增加 `image_read`，支持把图片转成 base64 后走多模态识别。
3. 改造命令执行等待逻辑，用 marker 轮询减少空等时间。
4. 给 `task_complete` 增加二次确认和检查清单。
5. 在直接调用 `litellm` 时补上 usage、reasoning、重试、上下文超限回退等逻辑。

## 4. 核心类与关键数据结构

### 4.1 `TerminusKira`

主类定义在 `terminus_kira/terminus_kira.py:214`，继承自 `Terminus2`。

它重写或新增的关键点有：

- `run()`：先保存原始任务指令，再交给父类总流程，见 `:297-302`
- `_get_parser()`：返回 `None`，因为不再走传统文本解析，见 `:304-306`
- `_get_prompt_template_path()`：指定系统 prompt 模板，见 `:308-314`
- `_handle_llm_interaction()`：改成直接调用 `litellm` 的 tools 能力，见 `:660-855`
- `_run_agent_loop()`：主 agent loop，加入 `image_read` 和双确认逻辑，见 `:857-1184`
- `_execute_commands()`：tmux 发送命令 + marker 轮询，见 `:234-288`
- `_execute_image_read()`：图片读取与多模态分析，见 `:497-580`

### 4.2 `ToolCallResponse`

定义在 `:61-68`，用于封装一次 LLM 返回：

- 普通文本 content
- tool_calls
- reasoning_content
- usage

### 4.3 `ImageReadRequest`

定义在 `:71-76`，用于承载 `image_read` 请求参数：

- `file_path`
- `image_read_instruction`

### 4.4 `TOOLS`

定义在 `:137-211`，这是 native tool calling 的关键：

- `execute_commands`
- `task_complete`
- `image_read`

这部分是整个 KIRA 版本区别于原始 Terminus2 的核心之一。

## 5. 启动与总调用入口

从仓库视角看，标准启动方式是：

```bash
uv run harbor run \
    --agent-import-path "terminus_kira.terminus_kira:TerminusKira" \
    ...
```

也就是说，真正的顶层 orchestrator 是 `harbor run`，它负责：

- 解析任务集
- 构造环境
- 创建 agent
- 调用 agent 的 `run()`

而 `TerminusKira.run()` 本身只做了一件额外的事：

- 保存 `self._original_instruction`
- 然后 `await super().run(...)`

见 `terminus_kira/terminus_kira.py:297-302`。

这也再次说明：`terminus_kira.py` 不是完整框架入口，而是被 `harbor` 宿主框架加载的核心 agent 类。

## 6. 主执行流程

下面按代码真实调用链整理。

### 6.1 入口阶段

1. `harbor run` 根据 `--agent-import-path` 加载 `TerminusKira`
2. 外部框架调用 `TerminusKira.run(instruction, environment, context)`
3. `run()` 保存原始任务文本到 `self._original_instruction`
4. 之后转交父类 `Terminus2.run()`

这里父类源码当前不在仓库中，但从子类重写点可以确定，父类后续会进入本类重写的 prompt/template/loop 逻辑。

### 6.2 Prompt 与 parser 阶段

父类在运行时会向子类取这几项配置：

- `_get_parser()` 返回 `None`
- `_get_prompt_template_path()` 返回 `prompt-templates/terminus-kira.txt`
- `_get_error_response_type()` 返回 `"response with valid tool calls"`

这代表 KIRA 版本不再要求模型输出某种 JSON/XML 文本再解析，而是直接让模型调用工具。

### 6.3 Agent loop 阶段：`_run_agent_loop()`

主循环位于 `:857-1184`。

每个 episode 大致做这些事：

1. 检查 tmux session 是否还活着
2. 如启用了 summarize，先尝试 proactive summarization
3. 建立本轮日志路径
4. 记录本轮之前的 token/cost 计数
5. 调用 `_handle_llm_interaction()` 获取模型下一步动作
6. 把模型结果写入 trajectory
7. 根据工具类型分支执行：
   - `image_read` 路径
   - shell commands 路径
8. 根据返回 observation 生成下一轮 prompt
9. 如果完成了双确认的 `task_complete`，则退出 loop

### 6.4 LLM 交互阶段：`_handle_llm_interaction()`

这是最重要的改造点，位于 `:660-855`。

它的流程是：

1. 将当前 `chat.messages` 复制出来
2. 追加本轮 user prompt
3. 调用 `_call_llm_with_tools(messages)`
4. 将 assistant 返回写回 chat history
5. 如果有 tool calls，则补写 OpenAI 风格的 `"role": "tool"` 占位消息
6. 更新 token / cache / cost
7. 处理两类异常：
   - `ContextLengthExceededError`
   - `OutputLengthExceededError`
8. 最后调用 `_parse_tool_calls()` 把 tool calls 转成内部可执行结构

返回值包含：

- `commands`
- `is_task_complete`
- `feedback`
- `analysis`
- `plan`
- `llm_response`
- `image_read`

也就是说，这一层完成了“LLM 原始输出 -> 内部动作计划”的转换。

## 7. Native tool calling 是如何工作的

### 7.1 工具定义

`TOOLS` 中定义了 3 个函数式工具：

#### `execute_commands`

参数包含：

- `analysis`
- `plan`
- `commands`

其中 `commands` 内每项含：

- `keystrokes`
- `duration`

#### `task_complete`

无参数，用于请求结束任务。

#### `image_read`

参数包含：

- `file_path`
- `image_read_instruction`

### 7.2 实际调用

在 `_call_llm_with_tools()` 中，代码直接构造：

- `model`
- `messages`
- `temperature`
- `tools=TOOLS`
- `timeout=900`

然后调用 `litellm.acompletion(...)`，见 `:598-658`。

这一步是整个 KIRA harness 的核心设计变化：不再依赖“让模型吐出一个格式正确的 JSON 文本，然后本地再 parse”，而是让模型直接返回结构化 `tool_calls`。

### 7.3 返回结果解析

`_extract_tool_calls()` 从 LiteLLM response 中取出工具调用，见 `:340-357`。

`_parse_tool_calls()` 再把这些工具调用转换为本地数据：

- `execute_commands` -> `list[Command]`
- `task_complete` -> `is_task_complete=True`
- `image_read` -> `ImageReadRequest`

见 `:379-455`。

## 8. Shell 命令执行流程

### 8.1 从 LLM 到命令对象

`_parse_tool_calls()` 会把 `execute_commands.commands[]` 中的每项转换成 `Command` 对象：

- `keystrokes`
- `duration_sec=min(duration, 60)`

这里顺便把最长等待时间裁到 60 秒，防止模型一次等待过久。

### 8.2 命令发送

真正的命令执行在 `_execute_commands()`，见 `:234-288`。

每条命令都会做下面几件事：

1. 给当前命令分配一个唯一 marker，如 `__CMDEND__7__`
2. 通过 `session.send_keys()` 把命令 keystrokes 发进 tmux
3. 再追加一条 `echo '<marker>'`

这样设计的含义是：

- shell 真正执行完前面的命令并重新回到 prompt 后，marker 才会出现
- harness 就能通过观测 marker 判断“命令其实已经结束了”

### 8.3 marker 轮询

发送完命令后，代码不会傻等满 `duration_sec`，而是：

1. 先短睡一会
2. 轮询 `session.capture_pane()`
3. 一旦看到 marker，就立即进入下一步

见 `:263-269`。

这就是 README 里说的 marker-based polling。它的作用是减少对快命令的无效等待。

### 8.4 输出清洗

所有命令完成后，代码会：

1. `session.get_incremental_output()`
2. 过滤掉 marker 行
3. 再做输出长度限制

最终把“清理后的终端输出”作为 observation 返回给下一轮模型。

## 9. 图片分析流程

### 9.1 触发条件

如果模型调用的是 `image_read`，则 `_run_agent_loop()` 走图片分支，见 `:1005-1088`。

### 9.2 文件读取

`_execute_image_read()` 会通过：

```bash
base64 <file_path>
```

从容器/环境中把图片读出来，见 `:513-521`。

这里不是走 tmux 打字，而是直接调用：

- `self._session.environment.exec(...)`

说明 `image_read` 走的是环境级命令执行，不是交互式终端输入。

### 9.3 多模态请求

随后它会：

1. 根据扩展名判断 MIME type，见 `:523-538`
2. 构造 `[{type: "text"}, {type: "image_url"}]` 形式的 multimodal message，见 `:540-552`
3. 调用 `_call_llm_for_image()` 发起第二次 LLM 请求，见 `:473-495`, `:556-564`

所以 `image_read` 本质上是“主 agent loop 中嵌套了一次专门的图像理解调用”。

### 9.4 结果回注

图片分析结果会被组织成：

`File Read Result for '<path>': ...`

然后作为 observation 反馈回下一轮 prompt。

## 10. 完成态确认流程

`task_complete` 不是一次调用就结束，而是双确认。

逻辑在命令分支和图片分支里都各做了一遍，见：

- 命令分支 `:1100-1117`, `:1179-1180`
- 图片分支 `:1015-1031`, `:1085-1086`

流程是：

1. 模型第一次调用 `task_complete`
2. harness 不立即结束，而是生成 `_get_completion_confirmation_message(...)`
3. 这条确认消息会附带：
   - 原始任务
   - 当前终端状态
   - checklist
4. 只有模型下一轮再次调用 `task_complete`，才真正退出 loop

这能降低“模型误判任务完成”的风险。

## 11. 上下文超限与容错流程

### 11.1 Context 超限

如果 `_call_llm_with_tools()` 抛出 `LiteLLMContextWindowExceededError`，会转换成 `ContextLengthExceededError`，见 `:629-632`。

随后 `_handle_llm_interaction()` 会：

1. 调用继承来的 `_unwind_messages_to_free_tokens(...)`
2. 尝试调用继承来的 `_summarize(...)`
3. 如果 summarization 失败，就退化为“原始任务 + 当前屏幕末尾内容”的简化 prompt
4. 用 summary prompt 再次调用 `_call_llm_with_tools()`

见 `:715-780`。

这部分说明：

- summarization 框架本体主要还是来自 `Terminus2`
- `TerminusKira` 做的是与 native tool calling 对接的接续处理

### 11.2 输出过长

如果模型输出因长度被截断，会触发 `OutputLengthExceededError`，见 `:782-825`。

此时 harness 会：

1. 向 chat history 注入一条错误反馈
2. 要求模型给出更短的响应
3. 再试一次 `_call_llm_with_tools()`

### 11.3 API 卡死保护

`_with_block_timeout()` 用 `asyncio.wait_for()` 包裹关键异步调用，超时后抛 `BlockError`，见 `:227-232`。

被包裹的典型操作包括：

- `environment.exec(...)`
- `session.is_session_alive()`
- `session.capture_pane()`
- summarization 相关调用

这属于基础设施层的防卡死保护。

### 11.4 重试机制

`_call_llm_with_tools()` 和 `_call_llm_for_image()` 都用了 `tenacity.retry`，见 `:457-472` 与 `:582-597`。

策略大意是：

- 最多重试 5 次
- 指数退避
- 对认证错误、BadRequest、上下文超限、输出超限等不做无意义重试

## 12. Trajectory / 观测记录流程

每个 episode 完成后，`_run_agent_loop()` 都会把本轮行为写入 trajectory，使用的数据结构来自 `harbor.models.trajectories`：

- `Step`
- `Observation`
- `ObservationResult`
- `ToolCall`
- `Metrics`

对应代码主要在：

- 图片分支 `:1033-1084`
- 命令分支 `:1118-1177`

记录内容包括：

- 模型消息
- reasoning_content
- 调用过的工具
- 观察结果
- token / cached token / cost

这说明 `terminus_kira.py` 不只是“会调模型和发命令”，它还是把 agent 行为结构化写回到父框架的 trajectory 体系里。

## 13. Prompt caching 在流程中的位置

`add_anthropic_caching()` 定义在 `anthropic_caching.py:7-62`。

作用是：

- 仅对 Anthropic/Claude 模型启用
- 对最近 3 条 message 添加 `cache_control: {"type": "ephemeral"}`

它被接入了两个地方：

- 普通 tool-calling 对话：`terminus_kira.py:607`
- 图片分析多模态调用：`terminus_kira.py:554`

因此 caching 是这个 harness 的一个横切优化层，而不是独立流程入口。

## 14. 可以把整体执行过程压缩成一张图

```text
harbor run
  -> 加载 TerminusKira
  -> TerminusKira.run()
    -> super().run() 进入 Terminus2 主框架
      -> 取 prompt template / parser / loop 配置
      -> TerminusKira._run_agent_loop()
        -> TerminusKira._handle_llm_interaction()
          -> TerminusKira._call_llm_with_tools()
            -> litellm.acompletion(tools=TOOLS)
          -> TerminusKira._parse_tool_calls()
        -> 分支执行
          -> execute_commands
             -> _execute_commands()
             -> tmux send_keys + marker polling
          -> image_read
             -> _execute_image_read()
             -> environment.exec(base64 file)
             -> _call_llm_for_image()
        -> 记录 trajectory / metrics
        -> 生成下一轮 prompt
        -> 若二次确认 task_complete，则退出
```

## 15. 如果只看“这个文件新增了什么”，重点是这 5 件事

1. 用 native tool calling 替代 ICL 文本解析。
2. 在 agent loop 里加入 `image_read` 多模态分支。
3. 用 marker-based polling 优化命令执行等待。
4. 增加更严格的完成确认机制。
5. 让上述能力与 `harbor` 原有的 chat、trajectory、summarization、session 体系兼容。

## 16. 最终判断

最终判断可以概括成一句话：

`terminus_kira/terminus_kira.py` 不是完整独立的 harness 框架，而是一个建立在 `harbor` / `Terminus2` 之上的核心 agent 扩展实现文件。  
它定义了这个项目最关键的 harness 行为差异，但要跑起来，仍然依赖外部 `harbor` 框架、prompt 模板、缓存工具以及运行脚本。

如果你之后想继续深挖，我建议下一步优先看两类内容：

1. `prompt-templates/terminus-kira.txt`，它定义了模型在 loop 中收到的任务格式。
2. `harbor.agents.terminus_2.Terminus2` 的源码，因为 session 创建、父类 `run()`、总结/切片、trajectory 落盘等底座能力主要在那里。
