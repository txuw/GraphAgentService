# API 层流式与同步契约说明

本文档说明当前 GraphAgentService API 层如何对外暴露 graph 的同步与流式能力，重点解释：

- API 层当前对外提供哪些接口
- 一次 `stream` 请求在 API 层是怎样被拆成“建连 + 执行 + 推送”的
- `sessionId / pageId / requestId / userId / traceId` 在接口语义上分别代表什么
- API 层如何把 LangGraph 原始流式事件转换成稳定的前端协议
- API 层与鉴权、SSE、Checkpoint 之间的边界应该怎样理解

本文档聚焦协议层与调用链，不讨论具体业务图的节点设计与 Prompt 细节。

## 1. 当前 API 层目标

当前 API 层的目标很明确：

- 保持现有 `/api/*` 路径体系不变
- 保持前端“先连 SSE，再发流式请求”的交互方式不变
- 对外统一返回稳定的 JSON / SSE 契约
- 将 LangGraph 内部运行细节尽量收敛在 service 层，不直接暴露给前端
- 允许在关闭鉴权时继续以匿名会话运行

可以先用一句话概括当前设计：

```text
API 路由只负责协议与上下文
-> service 负责调度 graph 与 SSE
-> graph 继续输出 LangGraph 原始事件
-> API 附近统一转换成前端友好的 AgentStreamEvent
```

这里统一的是“对外协议”，不是“内部 graph 只能按一种方式运行”喵～

## 2. 适用场景

当前 API 层主要覆盖两类调用场景：

### 流式场景

适合聊天、计划分析、工具调用这类需要边执行边展示的场景。

当前推荐交互方式是：

```text
GET  /api/sse/connect
-> SSE 建立成功
-> POST /api/graphs/{graph}/stream
-> HTTP 返回 requestId
-> 后续事件继续走前面的 SSE 连接
```

### 同步场景

适合一次性等待最终结果的场景。

当前入口是：

```text
POST /api/graphs/{graph}/invoke
```

它直接返回最终结果，不经过 SSE。

## 3. 相关代码位置

当前 API 层相关代码主要分布在以下位置：

- `src/graphagentservice/api/routes/graphs.py`
- `src/graphagentservice/api/routes/chat.py`
- `src/graphagentservice/api/dependencies.py`
- `src/graphagentservice/schemas/api.py`
- `src/graphagentservice/services/graph_stream_service.py`
- `src/graphagentservice/services/chat_stream_service.py`
- `src/graphagentservice/services/sse.py`
- `src/graphagentservice/services/agent_events.py`
- `src/graphagentservice/services/graph_service.py`
- `src/graphagentservice/common/trace.py`
- `src/graphagentservice/common/checkpoint.py`

职责边界可以先粗略理解为：

- `api/routes/*`：处理 HTTP 参数、响应模型、异常与依赖注入
- `schemas/api.py`：定义 API 输入输出与 SSE 事件模型
- `graph_stream_service.py`：驱动一次异步 graph 执行并向 SSE 分发事件
- `sse.py`：管理连接注册、心跳、编码与事件推送
- `agent_events.py`：负责对外事件工厂与 LangGraph 事件适配
- `graph_service.py`：执行 graph、本地校验 payload、拼装 trace 与 checkpoint 配置

## 4. API 层职责边界

当前 API 层只解决以下问题：

- 路由路径与请求方法
- 请求参数兼容与字段别名转换
- 统一响应包裹结构
- 请求级 `traceId`、`userId`、`request_headers` 透传
- SSE 建连与事件推送
- LangGraph 原始事件到对外协议的转换

当前 API 层**不**直接负责以下事情：

- graph 内部节点编排
- Prompt 设计
- 工具业务逻辑
- 模型 provider 选择细节
- checkpoint 具体存储实现

也就是说，API 层回答的是“前端该怎样调、能拿到什么”，而不是“graph 内部怎样思考”。

## 5. 对外接口

## 5.1 `GET /api/sse/connect`

用于建立 SSE 长连接。

请求参数：

- Query `sessionId`
- Query `pageId`
- Header `Last-Event-ID`

行为：

1. 解析或生成 `sessionId`
2. 解析或生成 `pageId`
3. 从请求上下文中尽量读取 `userId`
4. 注册到 `SseConnectionRegistry`
5. 立即推送一条 `connected` 事件
6. 后续持续推送业务事件或 `heartbeat`

响应头：

- `Cache-Control: no-cache`
- `Connection: keep-alive`
- `X-Accel-Buffering: no`

需要注意：

- 如果前端不传 `sessionId` 或 `pageId`，后端会自动生成
- 自动生成后，前端应从首条 `connected` 事件中取回并继续复用

## 5.2 `POST /api/graphs/{graph}/stream`

用于启动一次异步流式 graph 执行。

它的特点是：

- 不是直接返回 `text/event-stream`
- 只负责启动一次任务
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
“后端已经接收这次请求，请到已有 SSE 通道中继续收结果”
```

## 5.3 `POST /api/graphs/{graph}/invoke`

用于执行一次同步 graph 调用。

它的特点是：

- 不依赖 SSE 连接
- 直接返回统一的 `Result<T>`
- 更适合一次性拿最终结果的调用方

如果没有传 `sessionId`，后端也能执行，但会自动生成一个新的会话线程，因此不会延续既有 checkpoint 上下文。

## 5.4 `POST /api/chat/{graph}/execute`

这是兼容入口，内部本质上仍然复用流式分发逻辑。

它的价值主要是：

- 保持旧接口可继续工作
- 在不改路径的情况下兼容原有调用方式

但从当前 API 层设计角度看，更推荐将它视为兼容层，而不是后续新增能力的首选入口。

## 6. 请求生命周期

## 6.1 一次流式请求的完整生命周期

一次标准流式调用的处理顺序如下：

```text
前端调用 GET /api/sse/connect
-> API 层注册连接并返回 connected
-> 前端调用 POST /api/graphs/{graph}/stream
-> API 层校验会话与连接
-> GraphStreamDispatchService 创建后台任务
-> HTTP 立即返回 requestId
-> GraphService.stream_events() 驱动 LangGraph
-> AgentStreamEventAdapter 转换事件
-> SseConnectionRegistry 将事件推回已有 SSE 连接
```

这里最关键的设计是：

- HTTP 请求和流式输出被拆成两个阶段
- `requestId` 成为“本次执行”的唯一标识
- SSE 连接成为持续输出通道

## 6.2 路由层做了什么

以 `/api/graphs/{graph}/stream` 为例，路由层主要做这些事：

1. 解析 body / query 中的 `sessionId / pageId / requestId`
2. 做字段别名兼容，例如 `message -> text/query`
3. 构建 `GraphRequestContext`
4. 生成并返回 `X-Trace-Id`
5. 调用 `GraphStreamDispatchService.execute(...)`
6. 将错误统一转换为 HTTP 语义

这说明路由层是“协议适配层”，不直接参与 graph 运行。

## 6.3 `GraphStreamDispatchService` 做了什么

这层是 API 与 graph 之间最重要的调度层。

它主要负责：

- 检查目标 SSE 连接是否存在
- 生成或接收 `requestId`
- 提取请求级 `traceId`
- 将 `graph_name / session_id / page_id / user_id / request_id / trace_id` 组装为一次执行目标
- 创建 `AgentStreamEventFactory`
- 创建 `ToolEventEmitter`
- 驱动 `GraphService.stream_events(...)`
- 将 LangGraph 原始事件转换成对外事件
- 推送到对应 SSE 连接

也就是说，这层统一解决的是：

```text
一次 HTTP 请求
-> 怎样变成一次“可持续推送的异步图执行”
```

## 6.4 `GraphService` 做了什么

`GraphService` 在 API 链路中的职责主要有三类：

### payload 校验

它会按具体 graph 的 `input_model` 校验请求体。

### graph 执行

同步场景走 `invoke(...)`，流式场景走 `stream_events(...)`。

### 会话与 checkpoint 配置

它会把 `sessionId` 组装到 LangGraph 的 `configurable` 配置中，例如：

```text
thread_id     = {app_name}:{graph_name}:{sessionId}
checkpoint_ns = {app_name}:{graph_name}
```

因此在 API 语义上：

- 同一 graph 下，同一个 `sessionId` 对应同一条 checkpoint 线程
- 改变 `sessionId` 就等于开启新的会话线程

## 7. 请求标识语义

## 7.1 `sessionId`

`sessionId` 表示一次会话。

它在 API 层中同时承担两层语义：

- 前端会话标识
- checkpoint 线程定位键的一部分

对于流式调用，建议前端始终显式传递并复用同一个 `sessionId`。

## 7.2 `pageId`

`pageId` 表示页面或连接维度标识。

它的主要用途不是 graph 本身，而是：

- 区分同一会话下不同页面的 SSE 连接
- 尽量将事件推给正确的前端页面

虽然它现在是兼容字段，不是所有接口都强制要求，但在多标签页或多视图场景下仍然建议稳定传递。

## 7.3 `requestId`

`requestId` 表示一次具体请求。

它的主要作用是：

- 将一次 `stream` 调用和一串 SSE 事件关联起来
- 让前端在同一 `sessionId` 下区分多轮请求

如果前端不传，后端会自动生成。

## 7.4 `userId`

`userId` 只作为隔离维度，不是 API 运行前提。

当前行为是：

- 鉴权开启且拿到用户时，连接匹配会带上 `userId`
- 鉴权关闭或拿不到用户时，按匿名会话继续执行

因此 API 层不应该假设“必须有用户身份才能运行 graph”。

## 7.5 `traceId`

`traceId` 是请求级追踪标识。

当前行为是：

- 优先读取 `X-Trace-Id`
- 如果没有，就由后端自动生成
- 返回到 HTTP 响应头
- 同时出现在请求级 SSE 事件中

这样 API 调用日志、SSE 事件和后续观测链路可以串起来。

## 8. API 响应模型

## 8.1 统一 JSON 包裹：`ResultResponse`

当前同步接口与流式 ACK 都尽量统一为：

```json
{
  "code": 200,
  "msg": "success",
  "data": ...
}
```

这样做的好处是：

- 前端处理 JSON 接口时不需要区分太多响应外壳
- 流式启动接口和同步结果接口在外层结构上保持一致

## 8.2 SSE 事件模型：`AgentStreamEvent`

当前 API 层对外暴露的标准事件模型是 `AgentStreamEvent`。

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

它的设计目标不是完全复刻 LangGraph 原始事件，而是提供“前端可长期依赖”的稳定协议。

## 9. 对外事件协议

## 9.1 当前事件类型

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

可以先用一句话记住：

```text
connected / heartbeat 负责连接
plan_status / tool_* 负责过程
ai_token 负责正文
ai_done / ai_error 负责结束
```

## 9.2 当前事件对前端的含义

### `connected`

表示 SSE 通道已经可用。

### `heartbeat`

表示连接保活，一般不需要更新正文 UI。

### `plan_status`

表示 graph 当前阶段性状态，例如：

- 已接收请求
- 正在整理输入
- 已进入分析阶段
- 正在整理最终结果

### `ai_token`

表示 AI 正文增量，前端应直接拼接 `content`。

### `tool_start / tool_done / tool_error`

表示真实工具执行边界，而不是根据模型消息猜测出来的工具状态。

这对前端展示工具进度、对排障定位都更稳定。

### `ai_done / ai_error`

表示本轮请求正常结束或异常结束。

## 9.3 SSE 编码格式

最终发送给前端的仍然是标准 SSE frame：

```text
id: <eventId>
event: <eventType>
retry: 3000
data: <AgentStreamEvent JSON>
```

因此当前 API 层统一的是 `data` 内的事件协议，而不是重写 SSE 基础传输格式。

## 10. LangGraph 原始事件与 API 事件的关系

当前 `GraphService.stream_events(...)` 产出的原始事件更偏运行时，例如：

- `session`
- `updates`
- `messages`
- `result`
- `completed`

这些事件在 API 层不会原样透出，而是经过 `AgentStreamEventAdapter` 转换。

大致映射关系可以理解为：

```text
session   -> 不对外发送
updates   -> plan_status
messages  -> ai_token
result    -> 兜底补发 ai_token
completed -> ai_done
异常      -> ai_error
```

这样设计的原因是：

- LangGraph 原始事件适合后端编排与调试
- 对前端而言，它们过于底层且不稳定
- API 层需要提供面向产品交互的稳定语义

## 11. API 层兼容策略

当前 API 层做了几类兼容。

## 11.1 字段别名兼容

为了兼容现有前端，请求体支持以下别名：

- `text-analysis`：`message -> text`
- `plan-analyze`：`message -> query`
- `tool-agent`：`message -> query`
- `image-agent`：`imageUrl -> image_url`，`message/description -> text`
- `image-analyze-calories`：`imageUrl -> image_url`，`message/description -> text`

这使得旧前端不必一次性改完所有字段名。

## 11.2 Query 与 Body 的兼容

`sessionId / pageId / requestId` 既可以从 body 读取，也可以从 query 读取。

当前优先级是：

```text
优先 body
其次 query
```

这样可以兼容历史调用方式，同时避免强依赖某一种传参位置。

## 11.3 兼容聊天入口

`/api/chat/*/execute` 继续保留，用于兼容旧调用链。

但 API 层新能力应优先围绕 `/api/graphs/*` 扩展，避免协议分叉越来越多。

## 12. 与鉴权的边界

鉴权详细说明见：

- `doc/wiki/logto-auth.md`

在 API 层需要记住的只有几点：

- `/api/*` 请求会先进入统一鉴权依赖
- 鉴权开启时，`JWT.sub -> user_id`
- `user_id` 会进入 `request.state.current_user`
- 后续由 `build_graph_request_context()` 透传到 graph 运行链路
- 鉴权关闭时，仍然会返回匿名用户对象，而不是让链路失效

因此 API 层的正确假设应当是：

- “可能有用户”
- “也可能没有用户”
- “无论是否有用户，graph 都应该能跑”

## 13. 与 Checkpoint 的边界

Checkpoint 详细初始化在：

- `src/graphagentservice/common/checkpoint.py`

API 层与它的关系只有一层：

- API 通过 `sessionId` 影响 graph 的 `thread_id`

也就是说，API 层不直接操作 PG，但会通过会话标识影响：

- 是否命中既有会话上下文
- 是否开启新线程
- 同一会话下的状态是否延续

当前支持的 checkpoint 模式包括：

- `memory`
- `pg`
- `postgres`
- `postgresql`
- `disabled`

从 API 视角，最重要的结论是：

- 只要希望延续上下文，就必须稳定复用同一个 `sessionId`

## 14. 当前 API 层的一个关键保护：会话级串行执行

`GraphService` 内部现在对同一个 `graph_name + session_id` 做了串行协调。

它的作用是：

- 避免同一会话并发执行多份图任务
- 避免消息顺序混乱
- 避免工具调用回写交叉
- 避免 checkpoint 状态被并发覆盖

从 API 层看，这意味着：

- 同一会话下的多次请求最好按产品语义串行推进
- 如果确实要并发，就应使用不同 `sessionId`

## 15. 常见误区

### 误区 1：`stream` 接口会直接返回 SSE

不是。

当前 `/api/graphs/{graph}/stream` 返回的是普通 JSON ACK，真正的流式事件走已经建立好的 `/api/sse/connect` 连接。

### 误区 2：`userId` 是 graph 运行的必需条件

不是。

`userId` 只是连接隔离维度，拿不到时仍然可以按匿名会话执行。

### 误区 3：`requestId` 可以代替 `sessionId`

不可以。

- `sessionId` 表示会话
- `requestId` 表示某一轮请求

二者职责不同。

### 误区 4：不传 `pageId` 完全没影响

不完全对。

在单页面场景问题不大，但如果同一 `sessionId` 在多个页面都建立了 SSE 连接，不稳定传递 `pageId` 会让事件归属更难控制。

### 误区 5：前端可以直接依赖 LangGraph 原始事件名

不建议。

当前 API 层的设计目标就是隔离底层运行时细节，前端应优先依赖 `AgentStreamEvent`。

## 16. 扩展约定

后续如果继续扩展 API 层，建议遵守以下约定：

- 新 graph 优先挂到 `/api/graphs/{graph}/invoke|stream`
- 新事件类型优先扩展 `AgentStreamEvent`，不要直接向前端暴露底层运行时事件
- `userId` 继续只作为隔离维度，不提升为强依赖
- `ResultResponse` 继续作为默认 JSON 包裹结构
- 与前端有长期契约的字段优先保持兼容，不随内部 graph 结构轻易变化

如果需要更细看 SSE 传输层本身，可继续参考：

- `doc/wiki/sse.md`
