# GraphAgentService SSE 链路说明

本文档说明当前面向前端的 SSE 交互链路，并更新为本次重构后的实现：

- SSE 不再承担业务事件转换职责
- 内部事件统一先进入 `InProcessStreamEventBus`
- `SseStreamEventSink` 负责最终投影为 `AgentStreamEvent`
- `SseConnectionRegistry` 只负责连接与传输

## 1. 当前整体链路

当前完整链路如下：

```text
Frontend
  -> GET /api/sse/connect
  -> SseConnectionRegistry.register()
  -> send connected
  -> POST /api/graphs/{graph}/stream
  -> GraphStreamDispatchService.execute()
  -> GraphService.stream_events()
  -> LangGraphStreamAdapter / ToolStreamEventEmitter
  -> InProcessStreamEventBus.publish()
  -> SseStreamEventSink.publish()
  -> SseConnectionRegistry.publish_agent_event()
  -> Frontend 接收 AgentStreamEvent
```

这里有一个关键边界变化：

- graph 和 tool execution 只产生内部 `StreamEvent`
- 只有 `SseStreamEventSink` 才知道 `AgentStreamEvent`
- `SseConnectionRegistry` 不再参与内部语义适配

## 2. 为什么这样拆分

这次拆分的主要目标是把执行层和传输层彻底隔离：

- 防止“前端事件协议”反向污染 graph 执行链路
- 防止工具观测逻辑直接依赖 SSE registry
- 让后续新增 sink 时不需要改 graph 执行层
- 让 `AgentStreamEvent` 保持为稳定的前端契约，而不是内部事件模型

可以把它理解为三层：

### 执行层

- `GraphService.stream_events(...)`
- `ObservedToolNode`
- `ToolStreamEventEmitter`

职责：

- 产出内部 `StreamEvent`

### 分发与投影层

- `InProcessStreamEventBus`
- `SseStreamEventSink`

职责：

- 统一分发内部事件
- 把内部事件投影成对外 DTO

### SSE 传输层

- `SseConnectionRegistry`
- `SseConnection`
- `SseEventMessage`

职责：

- 管理连接
- 编码 SSE frame
- 输出到 HTTP 长连接

## 3. 对前端暴露的接口

### `GET /api/sse/connect`

用于建立 SSE 长连接。

请求参数：

- `sessionId`
- `pageId`
- Header `Last-Event-ID`

行为：

1. route 生成或解析 `sessionId / pageId`
2. 调用 `SseConnectionRegistry.register(...)`
3. 立即发送 `connected`
4. 返回 `text/event-stream`
5. 保持连接，持续输出业务事件或心跳

### `POST /api/graphs/{graph}/stream`

用于触发一次 graph 流式执行，但不直接返回 SSE。

行为：

1. 检查对应 SSE 连接是否已经存在
2. 调用 `GraphStreamDispatchService.execute(...)`
3. 返回一个普通 JSON ack
4. 真正的流式内容通过前面建立的 SSE 连接推送

### `POST /api/chat/*/execute`

这是兼容入口，内部只是复用 `GraphStreamDispatchService`。

新的前端接入建议优先使用 `/api/graphs/{graph}/stream`。

## 4. 事件是如何转换的

### 4.1 内部原始来源

当前执行期会产生两类事件来源：

#### LangGraph 原始流

来自 `GraphService.stream_events(...)`：

- `session`
- `updates`
- `messages`
- `result`
- `completed`

#### 工具执行边界

来自 `ObservedToolNode` + `ToolStreamEventEmitter`：

- tool start
- tool done
- tool error

### 4.2 统一转成内部 `StreamEvent`

内部 `StreamEvent` 的职责是统一表达：

- 事件目标是谁
- 事件类型是什么
- 该事件在当前 request 中的顺序
- 事件附带的业务文本、错误码和补充信息

其中关键字段有：

- `target`
- `kind`
- `seq`
- `event_id`
- `content`
- `code`
- `message`

### 4.3 最终投影为 `AgentStreamEvent`

`SseStreamEventSink` 是当前唯一的 SSE sink。

它负责：

1. 接收内部 `StreamEvent`
2. 投影成对外 `AgentStreamEvent`
3. 调用 `SseConnectionRegistry.publish_agent_event(...)`

对应关系如下：

```text
StreamEvent.kind = plan_status -> AgentStreamEvent.eventType = plan_status
StreamEvent.kind = ai_token    -> AgentStreamEvent.eventType = ai_token
StreamEvent.kind = tool_start  -> AgentStreamEvent.eventType = tool_start
StreamEvent.kind = tool_done   -> AgentStreamEvent.eventType = tool_done
StreamEvent.kind = tool_error  -> AgentStreamEvent.eventType = tool_error
StreamEvent.kind = ai_done     -> AgentStreamEvent.eventType = ai_done
StreamEvent.kind = ai_error    -> AgentStreamEvent.eventType = ai_error
```

终态规则：

- `ai_done.done = true`
- `ai_error.done = true`
- 其他事件 `done = false`

## 5. 当前对外事件语义

### `connected`

表示 SSE 连接已建立完成。

这条事件由 `SseConnectionRegistry.send_connected_event(...)` 直接构造，属于连接层事件，不进入 bus。

### `heartbeat`

表示连接保活。

这条事件由 `SseConnectionRegistry.event_stream(...)` 在超时场景下直接构造，同样属于连接层事件，不进入 bus。

### `plan_status`

表示 graph 阶段更新。

当前默认映射包括：

- `prepare` -> `GRAPH_NODE_UPDATED`
- `preprocess` -> `GRAPH_NODE_UPDATED`
- `analyze` -> `INTENT_RESOLVED`
- `agent` -> `GRAPH_NODE_UPDATED`
- `tools` -> `TOOLS_PREPARED`
- `finalize` -> `GRAPH_NODE_UPDATED`
- `empty` -> `GRAPH_NODE_UPDATED`

### `ai_token`

表示 AI 正文 token 或兜底正文文本。

规则：

- 正常 token 流时，来自 `messages`
- 如果前面没有正文 token，而 `result` 里能提取可展示文本，则补发一条 `ai_token`

### `tool_start / tool_done / tool_error`

表示真实工具执行边界。

当前 `content` 是 JSON 字符串，示例：

```json
{"toolName":"search_docs","phase":"start"}
```

错误时：

```json
{"toolName":"search_docs","phase":"error","errorMessage":"..."}
```

### `ai_done`

表示本轮请求正常结束。

### `ai_error`

表示本轮请求异常结束。

异常来源可能包括：

- graph 不存在
- payload 校验失败
- 模型构建失败
- MCP 配置或工具解析失败
- 其他流式执行异常

## 6. `sse.py` 的职责

当前 `src/graphagentservice/services/sse.py` 只负责连接与传输：

### 1. 定义 SSE 结构

- `SseEventMessage`
- `SseConnection`

### 2. 管理连接生命周期

- 注册连接
- 查找连接
- 校验连接存在
- 注销连接
- 替换旧连接

### 3. 按连接输出 SSE

- 把 `AgentStreamEvent` 编码成标准 SSE frame
- 压入连接队列
- 从异步队列持续输出

### 4. 处理心跳与断连清理

- 心跳定时发送
- 客户端断开检测
- 连接结束后的自动清理

一句话总结：

```text
sse.py 管连接，不管内部业务事件怎么产生
```

## 7. `SseConnectionRegistry` 的关键语义

### 7.1 连接主键

当前 registry 会按以下维度做连接匹配：

- `user_id`
- `session_id`
- `page_id`

这意味着：

- 同一用户、同一 session、同一 page 只保留一条活跃连接
- 匿名会话时，`user_id` 为空

### 7.2 单播与广播

当前投递语义：

- `page_id` 非空：只投递到目标页面
- `page_id` 为空：向匹配 `session_id + user_id` 的所有页面广播

### 7.3 `connected` 与 `heartbeat`

这两类事件属于连接层事件：

- 不经过内部 bus
- 不经过 `SseStreamEventSink`
- 由 registry 直接构造并发送

### 7.4 `Last-Event-ID`

当前已经接收 `Last-Event-ID` 并挂到连接上下文，但还没有做事件缓存与重放。

也就是说：

- 协议层面已预留续传字段
- 当前版本不提供 replay 能力

## 8. 为什么工具事件问题被根因修复了

本次 SSE 侧链路重构，顺带解决了旧设计里最容易引发协议耦合的问题：

- 旧实现会用一个自定义工具 wrapper 包住另一个 `BaseTool`
- wrapper 同时还直接持有 SSE registry
- 一旦工具层协议（如 `response_format="content_and_artifact"`）与 wrapper 行为不一致，就可能把观测需求捅到执行层

现在的做法是：

- 不再包装单个 `BaseTool`
- 只在 `ToolNode` 边界做观察
- 工具事件只发内部 `StreamEvent`
- SSE 由 sink 在最后一跳处理

这保证了：

- 工具执行协议和前端传输协议彻底分离
- SSE 故障不会反向污染 graph 主链路
- 前端事件模型可以稳定演进

## 9. 扩展建议

后续如果继续演进 SSE 能力，建议遵守以下方向：

- 如果要新增前端展示事件，优先扩展内部 `StreamEvent` 和 `SseStreamEventSink`
- 如果要支持更多下游通道，新增 sink，而不是改 graph 执行层
- 如果要支持断线续传，优先在 `SseConnectionRegistry` 增加事件缓存与 replay
- 如果要引入 MQ 或外部总线，保持 `StreamEventSink` 风格抽象，不要把外部总线协议推进 graph 执行层
