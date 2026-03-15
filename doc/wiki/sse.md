# OverMindAgent SSE 链路说明

本文档说明当前面向前端的 SSE 交互链路，重点解释：

- 接口层如何做 Convert
- `SseConnectionRegistry` 如何注册和管理连接
- `src/overmindagent/services/sse.py` 的主要职责
- `src/overmindagent/services/chat_stream_service.py` 的主要职责

目标是让前端消费到稳定、语义清晰的事件协议，同时尽量不改动内部 graph、node 和 LLM 逻辑。

## 1. 为什么要单独做一层 SSE 协议

当前 graph 内部流式能力来自 LangGraph `astream(..., version="v2")`，`GraphService.stream_events(...)` 暴露的原始事件是：

- `session`
- `updates`
- `messages`
- `result`
- `completed`

这些事件适合后端内部编排，但不适合直接暴露给前端聊天界面，主要原因是：

- `updates/messages/result` 偏底层实现细节
- 工具调用、状态更新、最终正文会混在一起
- 前端难以直接映射成“处理过程 + 正文”的双层展示结构

因此当前实现采用“接口层 Convert”方案：

1. graph / node 继续产出 LangGraph 原生事件
2. `GraphService` 继续负责 graph 调用与内部事件标准化
3. `ChatStreamService + SseEventAdapter` 在接口层附近做协议转换
4. 前端只消费面向聊天场景的 SSE 事件

这样可以把改动限制在 API 和 service 层，而不影响 graph 内部职责。

## 2. 对前端暴露的接口

### `GET /api/sse/connect`

用于建立 SSE 长连接。

请求参数：

- `sessionId`
- `pageId`
- Header `Last-Event-ID`

行为：

1. route 生成或解析 `session_id` / `page_id`
2. 调用 `SseConnectionRegistry.register(...)`
3. 立即发送 `connected` 事件
4. 返回 `text/event-stream`
5. 保持连接，持续输出事件或心跳

响应头：

- `Cache-Control: no-cache`
- `Connection: keep-alive`
- `X-Accel-Buffering: no`

### `POST /api/chat/execute`

用于触发一次聊天执行，但不直接返回 SSE。

请求体核心字段：

- `graph_name`
- `input`
- `session_id`
- `page_id`
- `request_id`

行为：

1. 检查对应 SSE 连接是否已经存在
2. 调用 `ChatStreamService.execute(...)`
3. 返回一个普通 JSON ack
4. 真正的流式内容通过前面建立的 SSE 连接推送

这种模式把“连接建立”和“任务执行”拆开，前端更容易管理页面级连接和会话级请求。

## 3. 整体链路

当前完整链路如下：

```text
Frontend
  -> GET /api/sse/connect
  -> SseConnectionRegistry.register()
  -> send connected
  -> POST /api/chat/execute
  -> ChatStreamService.execute()
  -> GraphService.stream_events()
  -> SseEventAdapter.adapt()
  -> SseConnectionRegistry.send()
  -> Frontend EventSource 接收 process / ai_token / ai_done / ai_error
```

职责边界：

- route：处理 HTTP 参数、响应头、依赖注入
- `GraphService`：产出内部 graph 事件
- `ChatStreamService`：驱动一次执行，把内部事件送进 Convert 和推送链路
- `SseEventAdapter`：内部事件到前端事件的映射器
- `SseConnectionRegistry`：连接管理、事件排队、心跳、编码与输出

## 4. Convert 规则

Convert 的核心原则是：

- 只有正文 token 进入 `ai_token`
- 状态、工具、节点更新全部进入 `process`
- 前端不直接感知 LangGraph 原始事件名

### 内部事件到前端事件的映射

`GraphService.stream_events(...)` 当前输出 `GraphStreamEvent(event, data)`。

`SseEventAdapter` 负责把它们转换成下面的前端协议：

- `connected`
- `heartbeat`
- `process`
- `ai_token`
- `ai_done`
- `ai_error`

具体规则如下。

### `session`

处理方式：

- 直接忽略
- 不下发给前端

原因：

- 连接建立成功已经由 `/api/sse/connect` 的 `connected` 事件表达
- 前端不需要再感知 graph 内部的 session 起始事件

### `updates -> process`

`updates` 表示 graph 节点状态更新。

Convert 后：

- 事件名固定为 `process`
- `code` 固定为 `GRAPH_NODE_UPDATED`
- `stage` 根据节点名映射
- `message` 生成轻量说明
- `meta` 保留 `ns` 和节点名

示例：

- `prepare` -> 已接收请求，准备开始处理
- `preprocess` -> 已接收请求，正在整理输入
- `tools` -> 正在执行工具调用
- `finalize` -> 正在整理最终结果

### `messages -> ai_token / process`

`messages` 是最重要的一类 Convert 场景。

当消息是普通 AI 文本增量时：

- 转成 `ai_token`
- `content` 直接追加到正文区

当消息包含工具调用时：

- 转成 `process`
- `code=TOOL_CALLING`
- `message` 描述准备调用的工具名

当消息是工具返回结果时：

- 转成 `process`
- `code=TOOL_RESULT`
- `content` 可以附带工具返回摘要

这样前端就能稳定实现：

- 正文只从 `ai_token.content` 累积
- 工具与状态类信息只进入“处理过程”列表

### `result -> ai_token` 兜底

正常情况下，正文应该已经通过 `messages` 中的 AI chunk 持续输出。

但有些 graph 可能没有正文 token 流，只在最终 `result` 里给出可显示文本，例如：

- `answer`
- `message`
- `analysis.summary`

这时 `SseEventAdapter` 会把 `result` 做一次兜底转换：

- 如果之前没有发过 `ai_token`
- 且 `result` 中能提取出可展示文本
- 就补发一条 `ai_token`

这保证了非 token 流 graph 也能接入同一套前端协议。

### `completed -> ai_done`

Convert 后：

- 事件名为 `ai_done`
- 携带 `status=completed`
- `stage=已完成`

前端可据此结束正文流式状态，并把阶段更新为“已完成”。

### 异常 -> `ai_error`

`ChatStreamService` 负责捕获执行过程中的异常，例如：

- graph 不存在
- payload 校验失败
- 模型构建失败
- 其他流式执行异常

异常不会再透出底层 event 名，而是统一转换为：

- `ai_error`

并附带：

- `code`
- `message`
- `stage=失败`

## 5. 如何 Registry SSE

SSE 注册与管理都放在 `src/overmindagent/services/sse.py`。

### 连接主键

当前 registry 以：

- `session_id`
- `page_id`

作为连接主键。

这样可以区分：

- 同一会话下不同页面
- 不同会话的独立连接

当前实现没有引入 `user_id`，因为项目还没有统一认证上下文。

### 注册流程

`SseConnectionRegistry.register(...)` 的行为：

1. 用 `(session_id, page_id)` 查已有连接
2. 如果已存在旧连接，先关闭旧连接
3. 创建新的 `SseConnection`
4. 保存到 registry
5. 返回连接对象

这保证同一个页面维度只保留一条活跃连接。

### 发送 `connected`

注册完成后，route 会调用：

```python
await sse_connection_registry.send_connected_event(connection)
```

这条事件负责告诉前端：

- 当前连接已建立
- 连接 id 是什么
- 关联了哪个 `session_id` / `page_id`
- `Last-Event-ID` 是什么

### 发送业务事件

`SseConnectionRegistry.send(...)` 负责：

1. 找到目标连接
2. 构造 `SseEventMessage`
3. 把消息压入连接的异步队列

真正的 HTTP 输出由 `event_stream(...)` 完成。

### 长连接输出与心跳

`event_stream(...)` 是 registry 对外的异步生成器。

它会：

- 持续从连接队列读取事件
- 编码成 SSE 文本块
- 超时后自动发送 `heartbeat`
- 检查客户端是否断开
- 在连接结束时自动注销

这样 route 不需要自己管理队列、心跳和连接清理。

### 事件 id 与顺序

每个 `SseConnection` 都维护自己的自增序号。

事件 id 生成规则为：

```text
{connection_id}:{sequence}
```

例如：

```text
2f...ab:1
2f...ab:2
2f...ab:3
```

它的作用是：

- 保证单连接内事件顺序稳定
- 为未来断线续传预留 `Last-Event-ID` 语义

### `Last-Event-ID` 的现状

当前已经接收 `Last-Event-ID` 并挂到连接上下文中，但还没有做事件缓存与重放。

也就是说：

- 协议层面已预留续传字段
- 当前版本不提供 replay 能力

## 6. `sse.py` 的主要职责

`src/overmindagent/services/sse.py` 主要负责四件事。

### 1. 定义 SSE 数据结构

包括：

- `SseEventMessage`
- `SseConnection`

其中：

- `SseEventMessage` 负责标准 SSE 编码
- `SseConnection` 负责保存连接级上下文和消息队列

### 2. 管理连接生命周期

包括：

- 注册连接
- 查找连接
- 校验连接存在
- 注销连接
- 替换旧连接

### 3. 管理发送顺序与编码

包括：

- 为每条事件生成递增 id
- 注入统一 `retry`
- 把 payload 编码成 SSE 文本

### 4. 处理心跳与断连清理

包括：

- 心跳定时发送
- 客户端断开检测
- 连接结束后的自动清理

总结一下，`sse.py` 负责的是“连接与传输层”，而不是“业务事件语义”。

## 7. `chat_stream_service.py` 的主要职责

`src/overmindagent/services/chat_stream_service.py` 负责的是“执行编排 + 协议转换”。

主要职责如下。

### 1. 验证连接存在

`execute(...)` 首先会调用 registry 的 `require(...)`，确保前端已经先建立了 SSE 连接。

如果连接不存在，直接报错，不会启动 graph 执行。

### 2. 启动一次异步执行任务

`execute(...)` 不会阻塞等待 graph 完成，而是：

- 生成 `request_id`
- 创建后台 task
- 立即返回 ack

这样前端可以：

- 先收到 `POST /api/chat/execute` 的成功响应
- 再从 SSE 连接里持续接收事件

### 3. 调用内部 graph 流式接口

后台任务里会调用：

```python
graph_service.stream_events(...)
```

这里仍然拿到的是内部事件：

- `session`
- `updates`
- `messages`
- `result`
- `completed`

### 4. 调用 `SseEventAdapter` 做 Convert

`SseEventAdapter` 是 `chat_stream_service.py` 里最关键的适配器。

它负责：

- 隐藏内部 event 名
- 区分正文 token 和过程事件
- 统一阶段文案
- 统一 `code/message/content/meta` 结构

也就是说：

- Convert 规则放在这里
- graph/node 内部完全不关心前端协议

### 5. 推送到具体连接

Convert 完成后，`ChatStreamService` 调用：

```python
await sse_connection_registry.send(...)
```

把适配后的事件送给指定 `session_id + page_id` 的连接。

### 6. 统一异常出口

执行期间如果发生异常，`ChatStreamService` 负责把异常转换成：

- `ai_error`

而不是让 route 或 graph 直接暴露底层异常细节给前端。

总结一下，`chat_stream_service.py` 负责的是“业务执行与协议适配层”，位于：

- 下游 `GraphService`
- 上游 `SseConnectionRegistry`

之间。

## 8. 当前职责分层

可以把当前 SSE 相关代码理解为下面这三层：

### Graph 内部层

- `graph builder`
- `node`
- `GraphRunContext`
- `GraphService.stream_events(...)`

职责：

- 执行业务图
- 产出内部流式事件

### Convert 编排层

- `ChatStreamService`
- `SseEventAdapter`

职责：

- 驱动一次执行
- 内部事件转前端事件
- 统一异常语义

### SSE 传输层

- `SseConnectionRegistry`
- `SseConnection`
- `SseEventMessage`

职责：

- 连接管理
- 事件排队
- 心跳
- SSE 编码
- 输出到 HTTP 长连接

这种分层的好处是：

- 前端协议变化时，优先改 Convert 层
- 连接策略变化时，优先改传输层
- graph 能力扩展时，尽量不影响 SSE 协议层

## 9. 扩展建议

后续如果继续演进 SSE 能力，优先按下面的方向扩展：

- 如果要支持断线续传，优先在 `SseConnectionRegistry` 增加事件缓存与 replay
- 如果要新增前端状态事件，优先扩展 `SseEventAdapter`
- 如果要支持更多聊天场景，优先保持 `GraphService.stream_events(...)` 不变，在 `ChatStreamService` 继续做场景化适配
- 如果要引入用户维度连接隔离，再把 registry 的主键从 `(session_id, page_id)` 扩成 `(user_id, session_id, page_id)`

当前实现的核心原则不变：

- graph 内部保持业务纯度
- SSE 协议转换停留在接口层附近
- 前端只消费面向聊天展示的稳定事件模型
