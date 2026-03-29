# React Native 对接 Graph 流式接口说明

本文档面向 React Native 前端，说明如何接入 GraphAgentService 的 graph 流式能力。

目标：

- 用一条 SSE 连接接收 graph 流式事件
- 用一个 HTTP `stream` 请求启动一次 graph 执行
- 正确处理 `sessionId / pageId / requestId`
- 正确消费 `AgentStreamEvent`

本文档只描述前端对接方式，不展开后端内部实现。

## 1. 推荐接入方式

推荐使用下面这套标准流程：

```text
1. 建立 SSE 连接：GET /api/sse/connect
2. 收到 connected 事件，拿到稳定的 sessionId / pageId
3. 发起 graph 流式请求：POST /api/graphs/{graph}/stream
4. 从 SSE 持续接收 AgentStreamEvent
5. 遇到 ai_done / ai_error 结束本轮请求
```

不要把 `POST /api/graphs/{graph}/stream` 理解成返回 SSE 的接口。

它只会返回一个普通 JSON ack：

```json
{
  "code": 200,
  "msg": "success",
  "data": "request-id"
}
```

真正的流式事件走已经建立好的 SSE 连接。

## 2. 关键标识语义

### `sessionId`

表示会话。

作用：

- 让同一轮对话命中同一条 checkpoint 线程
- 让多次请求能延续上下文

前端建议：

- 同一会话内稳定复用
- 只在需要“新开对话”时更换

### `pageId`

表示页面或连接维度。

作用：

- 区分同一 `sessionId` 下的不同 RN 页面或不同实例
- 避免事件投递到错误页面

前端建议：

- 每个页面实例生成一次并稳定复用
- 页面销毁后可以丢弃

### `requestId`

表示一次具体请求。

作用：

- 将某次 `stream` 请求和对应 SSE 事件串起来
- 让前端在同一 `sessionId` 下区分多轮请求

前端建议：

- 可以自己生成
- 也可以不传，让后端返回

### `traceId`

表示请求级追踪标识。

前端建议：

- 可选传 `X-Trace-Id`
- 也可以直接读取后端响应头里的 `X-Trace-Id`

## 3. 事件模型

前端通过 SSE 收到的 `data` 是 `AgentStreamEvent` JSON。

核心字段：

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

当前事件类型：

- `connected`
- `heartbeat`
- `plan_status`
- `ai_token`
- `tool_start`
- `tool_done`
- `tool_error`
- `ai_done`
- `ai_error`

## 4. 前端如何消费不同事件

### `connected`

表示 SSE 已连通。

`content` 是 JSON 字符串，通常包含：

- `connectionId`
- `userId`
- `sessionId`
- `pageId`
- `serverTime`
- `lastEventId`

前端建议：

- 解析 `content`
- 如果本地没有稳定的 `sessionId / pageId`，用这条事件回填

### `heartbeat`

表示连接保活。

前端建议：

- 不更新正文 UI
- 可用来刷新连接状态时间戳

### `plan_status`

表示 graph 阶段更新。

前端建议：

- 更新“处理中”状态文案
- 或在消息卡片上显示阶段进度

### `ai_token`

表示 AI 正文增量。

前端建议：

- 按 `requestId` 找到当前消息
- 直接拼接 `content`

### `tool_start / tool_done / tool_error`

表示工具执行边界。

当前 `content` 是 JSON 字符串，例如：

```json
{"toolName":"search_docs","phase":"start"}
```

或者：

```json
{"toolName":"search_docs","phase":"error","errorMessage":"..."}
```

前端建议：

- `JSON.parse(event.content || "{}")`
- 将工具状态展示到“处理中”区域，而不是正文区域

### `ai_done`

表示本轮请求结束。

前端建议：

- 标记该 `requestId` 对应消息已完成
- 关闭 loading / streaming 状态

### `ai_error`

表示本轮请求异常结束。

前端建议：

- 标记该 `requestId` 失败
- 展示 `message`
- 根据 `retriable` 决定是否展示“重试”按钮

## 5. 推荐状态模型

React Native 侧建议至少维护下面这些状态：

```ts
type StreamRequestState = {
  requestId: string
  sessionId: string
  pageId: string
  graphName: string
  text: string
  statusText?: string
  toolEvents: Array<{
    toolName: string
    phase: "start" | "done" | "error"
    errorMessage?: string
  }>
  done: boolean
  error?: {
    code?: string
    message?: string
    retriable?: boolean
  }
}
```

建议按 `requestId` 建立映射，而不是只按 `sessionId` 存状态。

## 6. React Native 接入建议

React Native 默认没有浏览器内建的 `EventSource` 行为，通常需要使用 SSE 兼容库。

常见做法有两种：

### 方案 A：使用 SSE 库

例如使用支持 RN 的 EventSource 类库。

优点：

- 和后端 SSE 协议天然匹配
- 处理事件名更直接

### 方案 B：通过原生层或自定义网络层封装 SSE

适合你们已经有统一长连接网络层的情况。

优点：

- 更容易和现有网络基础设施统一
- 更方便做鉴权、重连、日志上报

无论采用哪种方式，核心都一样：

- 先建立 SSE
- 再发起 `/api/graphs/{graph}/stream`
- 所有 UI 更新都来自 `AgentStreamEvent`

## 7. React Native 接入示例

下面示例只演示接入模式，事件源实现请替换成你们项目实际使用的 SSE 客户端。

```ts
type AgentStreamEvent = {
  sessionId?: string
  requestId?: string
  traceId?: string
  eventType: string
  eventId: string
  seq?: number
  content?: string
  done?: boolean
  finishReason?: string
  code?: string
  message?: string
  retriable?: boolean
}

type ToolPayload = {
  toolName?: string
  phase?: "start" | "done" | "error"
  errorMessage?: string
}

function safeParseJson<T>(raw?: string): T | null {
  if (!raw) return null
  try {
    return JSON.parse(raw) as T
  } catch {
    return null
  }
}
```

### 7.1 建立 SSE 连接

```ts
function connectGraphSse(params: {
  baseUrl: string
  sessionId?: string
  pageId?: string
  token?: string
  onEvent: (event: AgentStreamEvent) => void
}) {
  const { baseUrl, sessionId, pageId, token, onEvent } = params

  const query = new URLSearchParams()
  if (sessionId) query.set("sessionId", sessionId)
  if (pageId) query.set("pageId", pageId)

  const url = `${baseUrl}/api/sse/connect?${query.toString()}`

  const es = createYourEventSource(url, {
    headers: token ? { Authorization: `Bearer ${token}` } : undefined,
  })

  const handleMessage = (raw: { data: string }) => {
    const payload = JSON.parse(raw.data) as AgentStreamEvent
    onEvent(payload)
  }

  es.addEventListener("connected", handleMessage)
  es.addEventListener("heartbeat", handleMessage)
  es.addEventListener("plan_status", handleMessage)
  es.addEventListener("ai_token", handleMessage)
  es.addEventListener("tool_start", handleMessage)
  es.addEventListener("tool_done", handleMessage)
  es.addEventListener("tool_error", handleMessage)
  es.addEventListener("ai_done", handleMessage)
  es.addEventListener("ai_error", handleMessage)

  return es
}
```

### 7.2 发起 graph 流式请求

```ts
async function startGraphStream(params: {
  baseUrl: string
  graphName: string
  sessionId: string
  pageId: string
  requestId?: string
  body: Record<string, unknown>
  token?: string
}) {
  const { baseUrl, graphName, sessionId, pageId, requestId, body, token } = params

  const url = `${baseUrl}/api/graphs/${graphName}/stream`

  const res = await fetch(url, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
    },
    body: JSON.stringify({
      ...body,
      sessionId,
      pageId,
      requestId,
    }),
  })

  if (!res.ok) {
    throw new Error(`stream request failed: ${res.status}`)
  }

  const json = await res.json()
  return json.data as string
}
```

### 7.3 事件分发到 UI 状态

```ts
function reduceGraphEvent(
  current: StreamRequestState,
  event: AgentStreamEvent,
): StreamRequestState {
  switch (event.eventType) {
    case "plan_status":
      return {
        ...current,
        statusText: event.message || event.content || current.statusText,
      }

    case "ai_token":
      return {
        ...current,
        text: `${current.text}${event.content || ""}`,
      }

    case "tool_start":
    case "tool_done":
    case "tool_error": {
      const payload = safeParseJson<ToolPayload>(event.content)
      if (!payload?.toolName || !payload?.phase) {
        return current
      }
      return {
        ...current,
        toolEvents: [
          ...current.toolEvents,
          {
            toolName: payload.toolName,
            phase: payload.phase,
            errorMessage: payload.errorMessage,
          },
        ],
      }
    }

    case "ai_done":
      return {
        ...current,
        done: true,
      }

    case "ai_error":
      return {
        ...current,
        done: true,
        error: {
          code: event.code,
          message: event.message,
          retriable: event.retriable,
        },
      }

    default:
      return current
  }
}
```

## 8. 推荐交互顺序

推荐前端顺序如下：

1. 页面初始化时生成或恢复 `sessionId`
2. 页面初始化时生成 `pageId`
3. 建立 SSE 连接
4. 收到 `connected` 后再允许用户发起流式请求
5. 点击发送时，生成 `requestId`
6. 调用 `/api/graphs/{graph}/stream`
7. 后续只消费 SSE 事件，不轮询结果

## 9. 错误处理建议

### SSE 连接失败

建议：

- 展示“连接中断”
- 暂停发送按钮
- 自动重连后再恢复

### `stream` 请求返回 404

这通常表示对应 SSE 连接不存在或已经断开。

建议：

- 先重建 SSE
- 再重新发起本轮请求

### 收到 `ai_error`

建议：

- 以 `requestId` 为维度结束本轮消息
- 把 `message` 直接透给 UI
- `retriable=true` 时给出“重试”入口

## 10. 最佳实践

- 前端正文只从 `ai_token.content` 累积
- 工具事件和阶段事件不要混进正文
- 同一页面稳定复用 `pageId`
- 同一会话稳定复用 `sessionId`
- 按 `requestId` 组织单次请求状态
- 只把 `connected` 视为连接 ready 的信号
- 不要依赖 LangGraph 原始事件名

## 11. 一个最小接入心智模型

如果只记住一句话，可以记这个：

```text
SSE 负责收事件，POST /stream 负责发起任务，UI 只认 AgentStreamEvent
```

如需了解后端当前流式契约与 SSE 边界，可继续参考：

- `doc/wiki/api-stream-contract.md`
- `doc/wiki/sse.md`
