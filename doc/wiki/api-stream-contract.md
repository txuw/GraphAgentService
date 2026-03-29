# API 层流式与同步契约说明

本文档说明当前 GraphAgentService API 层如何对外暴露 graph 的同步与流式能力，并更新为本次重构后的单一路径实现：

- 执行层只产生内部 `stream event`
- 进程内异步 `event bus` 负责分发
- SSE 只负责连接管理与对外投递
- `AgentStreamEvent` 只作为前端 wire DTO

本文档聚焦 API 契约、调用链和前后端边界，不展开具体 graph 节点逻辑。

## 1. 当前目标

当前 API 层的目标是：

- 保持现有 `/api/*` 路径体系不变
- 保持“先建 SSE，再发 stream 请求”的前端交互方式不变
- 对外保持稳定的 `ResultResponse` 与 `AgentStreamEvent`
- 将 LangGraph 原始事件、工具生命周期和 SSE 传输彻底解耦
- 允许在关闭鉴权时继续以匿名会话运行

可以先用一句话概括当前链路：

```text
route
-> GraphStreamDispatchService
-> GraphService.stream_events() / ToolNode
-> internal StreamEvent
-> InProcessStreamEventBus
-> SseStreamEventSink
-> AgentStreamEvent
-> SseConnectionRegistry
```

## 2. 对外接口

### 2.1 `GET /api/sse/connect`

用于建立 SSE 长连接。

请求参数：

- Query `sessionId`
- Query `pageId`
- Header `Last-Event-ID`

行为：

1. 解析或生成 `sessionId`
2. 解析或生成 `pageId`
3. 从请求上下文中读取 `userId`
4. 注册到 `SseConnectionRegistry`
5. 立即推送一条 `connected` 事件
6. 后续持续推送业务事件或 `heartbeat`

响应头：

- `Cache-Control: no-cache`
- `Connection: keep-alive`
- `X-Accel-Buffering: no`

注意：

- 如果前端不传 `sessionId` 或 `pageId`，后端会自动生成
- 自动生成后，前端应从首条 `connected` 事件中取回并继续复用
- `Last-Event-ID` 当前仅做协议预留，不提供 replay

### 2.2 `POST /api/graphs/{graph}/stream`

用于启动一次异步流式 graph 执行。

它的特点是：

- 不直接返回 `text/event-stream`
- 只负责启动一次后台任务
- 同步返回本次请求的 `requestId`
- 真正的流式内容通过已经建立好的 SSE 连接返回

统一响应结构：

```json
{
  "code": 200,
  "msg": "success",
  "data": "request-id"
}
```

接口语义可以理解为：

```text
“后端已经接收这次请求，请继续从已有 SSE 连接里收结果”
```

### 2.3 `POST /api/graphs/{graph}/invoke`

用于执行一次同步 graph 调用。

它的特点是：

- 不依赖 SSE 连接
- 直接返回统一的 `Result<T>`
- 更适合一次性拿最终结果的调用方

如果没有传 `sessionId`，后端也能执行，但会开启新的会话线程，因此不会延续既有 checkpoint 上下文。

### 2.4 `POST /api/chat/*/execute`

这是兼容入口，内部只是复用 `GraphStreamDispatchService`。

建议：

- 新接入优先使用 `/api/graphs/{graph}/stream`
- `/api/chat/*/execute` 视为兼容层，不建议继续扩展新协议

## 3. 相关代码位置

当前 API 层相关代码主要分布在以下位置：

- `src/graphagentservice/api/routes/graphs.py`
- `src/graphagentservice/api/routes/chat.py`
- `src/graphagentservice/api/dependencies.py`
- `src/graphagentservice/schemas/api.py`
- `src/graphagentservice/services/graph_stream_service.py`
- `src/graphagentservice/services/stream_events.py`
- `src/graphagentservice/services/stream_event_bus.py`
- `src/graphagentservice/services/stream_event_sinks.py`
- `src/graphagentservice/services/tool_execution.py`
- `src/graphagentservice/services/sse.py`
- `src/graphagentservice/services/graph_service.py`
- `src/graphagentservice/common/trace.py`
- `src/graphagentservice/common/checkpoint.py`

职责边界：

- `api/routes/*`：处理 HTTP 参数、响应模型、异常与依赖注入
- `schemas/api.py`：定义 API 输入输出与对外 SSE 事件模型
- `graph_stream_service.py`：驱动一次异步 graph 执行并向 bus 发布事件
- `stream_events.py`：内部 `stream event` 模型、工厂与 LangGraph 适配
- `stream_event_bus.py`：进程内异步事件总线
- `stream_event_sinks.py`：内部事件到对外 DTO 的投影与下沉
- `tool_execution.py`：工具执行观察与 `ObservedToolNode`
- `sse.py`：连接注册、心跳、编码与事件推送
- `graph_service.py`：执行 graph、校验 payload、拼装 trace 与 checkpoint 配置

## 4. API 层职责边界

当前 API 层只解决以下问题：

- 路由路径与请求方法
- 请求参数兼容与字段别名转换
- 统一响应包裹结构
- 请求级 `traceId`、`userId`、`request_headers` 透传
- SSE 建连与事件推送
- 内部 `stream event` 到 `AgentStreamEvent` 的最终投影

当前 API 层不直接负责：

- graph 内部节点编排
- Prompt 设计
- 工具业务逻辑
- 模型 provider 选择细节
- checkpoint 具体存储实现
- observability / tracing 体系

## 5. 一次流式请求的完整生命周期

当前标准处理顺序如下：

```text
前端调用 GET /api/sse/connect
-> API 层注册连接并返回 connected
-> 前端调用 POST /api/graphs/{graph}/stream
-> API 层校验 session/page/request 参数
-> GraphStreamDispatchService 校验 SSE 连接存在
-> HTTP 立即返回 requestId
-> 后台 task 调用 GraphService.stream_events()
-> LangGraph 原始事件被 LangGraphStreamAdapter 转成内部 StreamEvent
-> 工具执行事件由 ObservedToolNode + ToolStreamEventEmitter 产出内部 StreamEvent
-> InProcessStreamEventBus 统一分发
-> SseStreamEventSink 投影为 AgentStreamEvent
-> SseConnectionRegistry 推送到匹配连接
```

这次重构后最关键的变化有三点：

- graph 事件和工具事件共用同一条主链路
- 执行层不再直接依赖 SSE registry
- `AgentStreamEvent` 不再承担内部事件模型职责

## 6. `GraphStreamDispatchService` 的职责

这层是 HTTP 请求和 graph 执行之间的调度中心。

它主要负责：

- 检查目标 SSE 连接是否存在
- 生成或接收 `requestId`
- 提取请求级 `traceId`
- 组装 `StreamEventTarget`
- 创建共享的 `StreamEventSequence`
- 创建 `StreamEventFactory`
- 创建 `LangGraphStreamAdapter`
- 创建 `ToolStreamEventEmitter`
- 驱动 `GraphService.stream_events(...)`
- 将所有事件统一发布到 `InProcessStreamEventBus`

它不再做的事情：

- 不再直接调用 `SseConnectionRegistry.publish_agent_event(...)`
- 不再构造面向前端的 DTO
- 不再持有旧的 `ToolEventEmitter -> SSE` 侧链

## 7. 内部事件与对外 DTO

### 7.1 内部事件：`StreamEvent`

内部事件位于 `services/stream_events.py`，用于统一表达执行过程。

核心字段包括：

- `target`
- `kind`
- `seq`
- `event_id`
- `content`
- `code`
- `message`
- `retriable`
- `finish_reason`

其中 `target` 包含：

- `graph_name`
- `session_id`
- `request_id`
- `trace_id`
- `user_id`
- `page_id`

### 7.2 对外事件：`AgentStreamEvent`

`AgentStreamEvent` 位于 `schemas/api.py`，只作为 SSE `data` 的稳定 JSON 契约。

常用字段包括：

- `sessionId`
- `requestId`
- `traceId`
- `eventType`
- `eventId`
- `seq`
- `content`
- `done`
- `finishReason`
- `code`
- `message`
- `retriable`

当前只有 `SseStreamEventSink` 知道如何把 `StreamEvent` 投影成 `AgentStreamEvent`。

## 8. 当前对外事件类型

当前主要事件类型包括：

- `connected`
- `heartbeat`
- `plan_status`
- `ai_token`
- `tool_start`
- `tool_done`
- `tool_error`
- `ai_done`
- `ai_error`

可以记为：

```text
connected / heartbeat 负责连接
plan_status / tool_* 负责过程
ai_token 负责正文
ai_done / ai_error 负责结束
```

### `connected`

表示 SSE 通道已经建立完成。

### `heartbeat`

表示连接保活，一般不更新正文 UI。

### `plan_status`

表示 graph 当前阶段性状态，例如：

- 已接收请求，正在分析你的诉求
- 已接收请求，正在整理输入
- 已进入分析阶段，正在处理你的请求
- 已准备工具调用，正在查询所需数据
- 正在整理最终结果

### `ai_token`

表示 AI 正文增量，前端应直接拼接 `content`。

### `tool_start / tool_done / tool_error`

表示真实工具执行边界，来自 `ObservedToolNode` 的执行观察，而不是通过模型文本推断。

当前 `content` 是 JSON 字符串，示例：

```json
{"toolName":"search_docs","phase":"start"}
```

错误时可能包含：

```json
{"toolName":"search_docs","phase":"error","errorMessage":"..."}
```

### `ai_done / ai_error`

表示本轮请求正常结束或异常结束。

- `ai_done.done = true`
- `ai_error.done = true`

## 9. LangGraph 原始事件与对外事件的关系

当前 `GraphService.stream_events(...)` 产出的原始事件仍然更偏运行时，例如：

- `session`
- `updates`
- `messages`
- `result`
- `completed`

它们不会原样暴露给前端，而是先被适配为内部 `StreamEvent`，再经 sink 投影为 `AgentStreamEvent`。

当前映射关系如下：

```text
session   -> 不对外发送
updates   -> plan_status
messages  -> ai_token
result    -> 如果前面没有正文 token，则兜底补发 ai_token
completed -> ai_done
异常      -> ai_error
```

工具生命周期事件不是从 LangGraph 原始流里猜测，而是通过 `ObservedToolNode` 直接发出：

```text
tool call start -> tool_start
tool call ok    -> tool_done
tool call error -> tool_error
```

## 10. 请求标识语义

### 10.1 `sessionId`

表示一次会话，同时也是 checkpoint 线程定位键的一部分。

建议前端在同一会话内稳定复用。

### 10.2 `pageId`

表示页面或连接维度标识，用于区分同一会话下的不同 SSE 连接。

语义：

- `pageId` 非空：只投递到目标页面
- `pageId = null`：向匹配 `sessionId + userId` 的所有页面广播

### 10.3 `requestId`

表示一次具体请求，用于将一次 `stream` 调用与一串 SSE 事件关联起来。

如果前端不传，后端会自动生成。

### 10.4 `userId`

只作为连接隔离维度，不是 graph 运行前提。

- 鉴权开启且拿到用户时，连接匹配带上 `userId`
- 鉴权关闭或拿不到用户时，按匿名会话继续执行

### 10.5 `traceId`

表示请求级追踪标识。

当前行为：

- 优先读取 `X-Trace-Id`
- 如果没有，则由后端自动生成
- 返回到 HTTP 响应头
- 同时出现在请求级 SSE 事件中

## 11. 字段兼容策略

### 11.1 Body 字段别名

为了兼容现有调用方，请求体支持以下别名：

- `text-analysis`：`message -> text`
- `plan-analyze`：`message -> query`
- `tool-agent`：`message -> query`
- `image-agent`：`imageUrl -> image_url`，`message/description -> text`
- `image-analyze-calories`：`imageUrl -> image_url`，`message/description -> text`

### 11.2 Query 与 Body 并存

`sessionId / pageId / requestId` 既可以从 body 读取，也可以从 query 读取。

优先级：

```text
优先 body
其次 query
```

## 12. 与鉴权、Checkpoint 的边界

鉴权详见 `doc/wiki/logto-auth.md`。

API 层只需要记住：

- `/api/*` 请求先进入统一鉴权依赖
- 鉴权开启时，`JWT.sub -> user_id`
- `user_id` 会进入 `request.state.current_user`
- 后续由 `build_graph_request_context()` 透传到 graph 运行链路
- 鉴权关闭时，仍然返回匿名用户对象，而不是让链路失效

Checkpoint 详见 `src/graphagentservice/common/checkpoint.py`。

从 API 视角，最重要的结论是：

- 同一 graph 下，同一 `sessionId` 对应同一条 checkpoint 线程
- 如果希望延续上下文，就必须稳定复用同一个 `sessionId`

## 13. 常见误区

### 误区 1：`stream` 接口会直接返回 SSE

不是。

`/api/graphs/{graph}/stream` 返回的是普通 JSON ACK，真正的流式事件走 `/api/sse/connect`。

### 误区 2：前端可以直接依赖 LangGraph 原始事件名

不建议。

前端应只依赖 `AgentStreamEvent`。

### 误区 3：工具事件还是旧的旁路直推 SSE

不是。

现在工具事件和 graph 事件已经统一进入同一条 `stream event -> bus -> sink -> SSE` 主链路。

### 误区 4：`AgentStreamEvent` 还是内部事件模型

不是。

现在它只是前端 wire DTO。

## 14. 扩展约定

后续继续扩展 API 层时，建议遵守以下约定：

- 新 graph 优先挂到 `/api/graphs/{graph}/invoke|stream`
- 新内部事件优先扩展 `StreamEvent`
- 新前端协议字段优先通过 sink/projector 投影，不要让执行层直接感知 DTO
- `userId` 继续只作为隔离维度，不提升为强依赖
- 与前端有长期契约的字段优先保持兼容

如果需要更细看 SSE 传输层本身，可继续参考 `doc/wiki/sse.md`。
