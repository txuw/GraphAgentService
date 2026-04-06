# Plan Analyze 中断提问与恢复链路说明

本文档说明当前 `plan-analyze` graph 的中断提问、恢复执行与前后端交互方式。

重点包括：

- `ask_user_questions -> interrupt -> resume` 的链路设计
- `plan-analyze` 当前 graph 拓扑与执行阶段
- 前端如何消费 `interrupt` 事件并提交答案
- `sessionId / pageId / requestId` 在这条链路中的语义

本文档聚焦当前实现与对接边界，不展开通用 SSE 基础概念。  
如需补充阅读，可继续参考：

- `doc/wiki/api-stream-contract.md`
- `doc/wiki/react-native-graph-integration.md`
- `doc/wiki/sse.md`

## 1. 当前目标

当前 `plan-analyze` 链路的目标是：

- 支持模型在信息不足时主动向用户发起结构化追问
- 支持 graph 在追问点暂停，并在用户回答后继续执行
- 保持 SSE 作为统一事件返回通道
- 保持前端对接协议稳定，不依赖模型自然语言追问
- 在执行前后分别接入记忆召回与记忆提交

可以先用一句话概括当前链路：

```text
前端发起 /stream
-> graph 执行到 ask_user_questions 时触发 interrupt
-> SSE 返回结构化问题
-> 前端调用 /resume 提交 answers
-> graph 从原中断点继续执行直到 ai_done / ai_error
```

## 2. 当前 graph 拓扑

当前 `plan-analyze` 拓扑如下：

```text
START
-> memory_recall
-> analyze
-> tools
-> analyze
-> ...（按需循环）
-> memory_commit
-> END
```

各节点职责如下：

- `memory_recall`
  - 从 Mem0 检索用户相关记忆
  - 将召回内容注入分析上下文
- `analyze`
  - 读取用户输入、历史消息、召回记忆
  - 绑定 MCP 工具和 `ask_user_questions`
  - 决定继续输出、调用工具，或进入追问
- `tools`
  - 执行真实工具调用
  - 包含 MCP 工具，也包含 `ask_user_questions`
- `memory_commit`
  - 在本轮完成后异步提交记忆
  - 不阻塞主回复链路

这条拓扑的关键特征是：`plan-analyze` 不再是单段式输出 graph，而是一个允许暂停和恢复的状态机。

## 3. 首次执行链路

当前首次请求的标准处理顺序如下：

```text
前端建立 SSE
-> POST /api/graphs/plan-analyze/stream
-> GraphStreamDispatchService.execute()
-> GraphService.stream_events()
-> plan-analyze graph 执行
-> 若模型调用 ask_user_questions
-> LangGraph interrupt
-> LangGraphStreamAdapter 转为 interrupt 事件
-> SseStreamEventSink 投影为 AgentStreamEvent
-> 前端展示问题卡片
```

### 3.1 `ask_user_questions` 的定位

`ask_user_questions` 是本地工具，不是普通文本提示。

它的职责是：

- 接收结构化问题列表
- 调用 `langgraph.types.interrupt(...)`
- 暂停 graph 执行
- 将问题 JSON 透传给前端

因此前端拿到的是结构化问题数据，而不是一段需要再解析的自然语言。

### 3.2 中断不是错误

当前 `ObservedToolNode` 对 `GraphInterrupt` 做了专门处理：

- 不会将其视为工具失败
- 不会发出 `tool_error`
- 会继续交给 LangGraph 管理暂停状态

从事件语义上，应将 `interrupt` 理解为：

```text
当前 request 已暂停，正在等待用户输入
```

而不是：

```text
当前 request 执行失败
```

### 3.3 中断后的结束规则

当前实现中，一旦检测到 interrupt：

- `GraphService` 不再补发 `result`
- `GraphService` 不再补发 `completed`
- `LangGraphStreamAdapter` 会抑制后续 `result/completed` 投影
- `SseStreamEventSink` 会把 `interrupt` 标记为终态事件，`done = true`

也就是说：

```text
interrupt = 当前请求以“等待回答”方式结束
ai_done    = 当前请求以“正文生成完成”方式结束
```

两者是不同的终态。

## 4. 恢复执行链路

用户完成选择后，前端应显式调用恢复接口：

```text
前端提交 answers
-> POST /api/graphs/plan-analyze/resume
-> GraphStreamDispatchService.resume()
-> GraphService.resume_stream_events()
-> Command(resume=answers)
-> graph 从 checkpoint 中断点继续执行
-> 继续发 plan_status / tool_* / ai_token / ai_done
```

当前恢复链路依赖以下条件：

- 使用与中断前相同的 `sessionId`
- 对应 SSE 连接仍然存在
- graph 已启用 checkpoint

如果 `sessionId` 变化，恢复请求将无法接到原来的暂停状态。

## 5. 对外接口

### 5.1 首次发起执行

首次执行接口如下：

```text
POST /api/graphs/plan-analyze/stream
```

接口职责：

- 接收请求并启动后台任务
- 返回本次请求的 `requestId`
- 真正的流式事件继续通过 SSE 返回

### 5.2 恢复执行

恢复接口如下：

```text
POST /api/graphs/plan-analyze/resume
```

请求参数：

- Query `sessionId`，必填
- Query `pageId`，可选
- Query `requestId`，可选

请求体：

```json
{
  "answers": {
    "training_goal": "减脂优先",
    "weekly_frequency": "每周 4 次"
  }
}
```

也兼容：

```json
{
  "resumeValue": {
    "training_goal": "减脂优先"
  }
}
```

响应仍为普通 ack：

```json
{
  "code": 200,
  "msg": "success",
  "data": "request-id"
}
```

建议前端使用 `interrupt.content` 里的 `question_id` 作为 `answers` 的 key，避免靠问题文案匹配。

## 6. 当前对外事件

当前与这条链路相关的事件主要包括：

- `plan_status`
- `tool_start`
- `tool_done`
- `tool_error`
- `interrupt`
- `ai_token`
- `ai_done`
- `ai_error`

### 6.1 `interrupt`

`interrupt` 是本次链路新增的关键事件。

当前特征如下：

- `eventType = "interrupt"`
- `code = "INTERRUPTED"`
- `message = "等待用户回答"`
- `done = true`
- `content` 为问题列表 JSON 字符串

`content` 的典型结构如下：

```json
[
  {
    "question_id": "training_goal",
    "header": "训练目标",
    "question": "你这周更希望优先达成哪个目标？",
    "options": [
      {
        "label": "减脂优先（推荐）",
        "description": "提高热量消耗，训练安排偏向减脂"
      },
      {
        "label": "增肌优先",
        "description": "增加力量训练比重和恢复安排"
      }
    ],
    "multiSelect": false
  }
]
```

前端需要自己执行：

```ts
JSON.parse(event.content || "[]")
```

### 6.2 `ask_user_questions` 的工具事件特征

如果中断来自 `ask_user_questions`，前端通常会看到：

```text
tool_start(ask_user_questions)
-> interrupt
```

当前实现不会在中断时补发：

- `tool_done`
- `tool_error`

因此前端不要将“未收到 `tool_done`”视为异常。

### 6.3 阶段状态

由于 graph 拓扑包含记忆读写，当前 `plan_status` 可能出现以下阶段文案：

- `memory_recall` -> `正在检索相关记忆`
- `analyze` -> `已进入分析阶段，正在处理你的请求`
- `tools` -> `已准备工具调用，正在查询所需数据`
- `memory_commit` -> `正在保存记忆`

前端如果有“处理中”状态区域，需要兼容这些阶段。

## 7. 前端对接建议

### 7.1 推荐交互顺序

推荐前端按下面顺序接入：

```text
1. 建立 SSE
2. 发起 /stream
3. 监听 plan_status / tool_* / ai_token / interrupt / ai_done / ai_error
4. 收到 interrupt 后展示问题卡片
5. 收集答案后调用 /resume
6. 继续在同一条 SSE 连接上接收恢复后的事件
```

### 7.2 状态模型建议

前端不要只保留 `streaming / done` 二元状态，至少建议区分：

- `streaming`
- `waiting_for_answers`
- `resuming`
- `completed`
- `failed`

推荐对应规则：

- 收到 `interrupt` 时进入 `waiting_for_answers`
- 发起 `/resume` 后进入 `resuming`
- 收到 `ai_done` 后进入 `completed`
- 收到 `ai_error` 后进入 `failed`

### 7.3 标识管理建议

推荐前端按下面方式处理标识：

- `sessionId`
  - 一次会话内稳定复用
  - 中断恢复时必须保持不变
- `pageId`
  - 页面实例级稳定复用
  - 用于继续复用当前 SSE 连接
- `requestId`
  - 用于组织单次请求片段
  - 恢复后通常会得到新的 `requestId`

一个更适合前端实现的心智模型是：

```text
同一条对话消息可以包含多个 requestId 片段，
但这些片段共享同一个 sessionId
```

### 7.4 答案提交建议

前端建议将答案组织为：

```json
{
  "answers": {
    "question_id_1": "option_a",
    "question_id_2": "option_b"
  }
}
```

不建议：

- 将答案重新拼成自然语言再发回聊天输入
- 依赖问题文案字符串做回传键
- 将 `interrupt` 视为错误态

## 8. 前后端职责边界

当前这条链路的职责边界如下：

### 后端负责

- 判断是否需要追问
- 产出结构化问题列表
- 在追问点触发 interrupt
- 通过 resume 恢复 graph
- 通过 checkpoint 保持会话状态

### 前端负责

- 建立和维护 SSE 连接
- 将 `interrupt.content` 渲染成问题卡片
- 收集用户选择并组装为 `answers`
- 恢复时继续复用相同的 `sessionId / pageId`
- 将多个 `requestId` 片段组织为同一轮对话体验

### 前端不应负责

- 猜测什么时候应该追问
- 解析模型自然语言问题文本
- 在恢复时更换 `sessionId`
- 将 `interrupt` 当成普通错误态处理

## 9. 记忆链路的影响

虽然本文重点是中断与恢复，但记忆链路同样会影响交互表现。

### 9.1 Recall 在前

`memory_recall` 在 `analyze` 之前执行，因此模型在决定是否追问前，已经可以看到：

- 当前用户标识
- 召回到的相关历史偏好

这会减少一部分本可由记忆补足的信息追问。

### 9.2 Commit 在后

`memory_commit` 只会在本轮执行完成后触发。

因此：

- 中断时不会先写回记忆
- 只有恢复后真正执行到结束路径，才会异步提交记忆

从交互角度可以理解为：

```text
用户完成回答并走完整个执行链路后，
这轮对话才算真正闭环
```

## 10. 相关代码位置

当前链路的关键代码主要位于以下位置：

- `src/graphagentservice/graphs/plan_analyze/builder.py`
- `src/graphagentservice/graphs/plan_analyze/nodes.py`
- `src/graphagentservice/graphs/plan_analyze/tools.py`
- `src/graphagentservice/services/graph_service.py`
- `src/graphagentservice/services/graph_stream_service.py`
- `src/graphagentservice/services/stream_events.py`
- `src/graphagentservice/services/stream_event_sinks.py`
- `src/graphagentservice/services/tool_execution.py`
- `src/graphagentservice/api/routes/graphs.py`
- `src/graphagentservice/schemas/api.py`
- `src/graphagentservice/memory/provider.py`
- `src/graphagentservice/memory/commit.py`

如果只按职责归类，可以记为：

- graph 拓扑与节点逻辑：`graphs/plan_analyze/*`
- 中断与恢复执行：`graph_service.py`、`graph_stream_service.py`
- 事件投影：`stream_events.py`、`stream_event_sinks.py`
- HTTP 契约：`api/routes/graphs.py`、`schemas/api.py`
- 记忆读写：`memory/*`

## 11. 常见误区

### 误区 1：`interrupt` 表示失败

不是。

它表示当前请求正在等待用户输入，是暂停态，不是失败态。

### 误区 2：恢复时继续调用 `/stream` 即可

不是。

恢复必须走 `/api/graphs/plan-analyze/resume`。

### 误区 3：恢复时可以换 `sessionId`

不可以。

恢复依赖原 checkpoint 线程，`sessionId` 变化后将无法接到原中断状态。

### 误区 4：`interrupt` 后还会继续收到 `ai_done`

当前实现不会。

收到 `interrupt` 表示这一段请求已经以“等待回答”的方式结束。

### 误区 5：前端可以直接依赖自然语言追问文本

不建议。

当前设计的目标就是让前端依赖结构化问题数据，而不是依赖模型自然语言输出。

## 12. 结论

当前 `plan-analyze` 的中断提问链路，本质上是一条“结构化追问 + checkpoint 恢复”的多阶段交互链路。

如果只记住一句话，可以记这个：

```text
SSE 负责返回 interrupt 和后续事件，
前端负责提交 answers，
后端负责用同一个 sessionId 从中断点继续把 graph 跑完。
```
