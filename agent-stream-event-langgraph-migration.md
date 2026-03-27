# AgentStreamEvent 迁移说明（前端契约优先版）

## 1. 文档目标

本文档用于指导将当前流式输出协议迁移到 LangGraph，并明确：

- 前后端交互逻辑
- 前端需要依赖的接口规范
- 前端会收到哪些事件
- 每种事件的字段、触发时机、顺序约束
- 哪些内容必须保持兼容，才能最大程度减少前端适配成本

这份文档是交给下一个 LLM 的实现说明，核心目标不是复刻 Spring AI Alibaba 的内部业务图，而是：

**在后端内部实现已经重写的前提下，尽可能保持前端交互方式和事件协议不变。**

---

## 2. 本次迁移的核心原则

### 2.1 第一优先级

第一优先级不是迁移旧业务图，而是：

- 保留前端接口调用方式
- 保留前端事件消费方式
- 保留 `AgentStreamEvent` 作为对外事件协议
- 尽量不让前端改事件解析逻辑

### 2.2 不迁移范围

以下内容**不是本次迁移的兼容重点**：

- Spring AI Alibaba 原有节点结构
- 原有 Graph 内部状态设计
- 原有工具编排细节
- 原有记忆写回实现细节
- 原有状态事件是由哪个节点触发

也就是说：

- 旧业务逻辑可以完全重写
- LangGraph 内部如何编排可以自由设计
- 只要对前端暴露的接口与事件契约稳定即可

### 2.3 真正要保持兼容的内容

真正应该优先保持兼容的是：

1. 接口路径、方法、入参结构、返回结构
2. SSE 帧格式
3. `AgentStreamEvent` 字段结构
4. 事件类型名称
5. 事件触发时机的大体语义
6. 请求级终态语义

---

## 3. 旧实现只作为协议参考，不作为业务实现参考

旧实现中，下列类只用于提取“当前前端契约”，不表示这些实现本身要迁移：

- `sport-assistant-ai/src/main/java/com/txuw/top/cloud/sport/assistant/ai/model/AgentStreamEvent.java`
- `sport-assistant-ai/src/main/java/com/txuw/top/cloud/sport/assistant/ai/service/AgentSseConnectionRegistry.java`
- `sport-assistant-ai/src/main/java/com/txuw/top/cloud/sport/assistant/ai/service/AgentService.java`
- `sport-assistant-ai/src/main/java/com/txuw/top/cloud/sport/assistant/ai/security/McpToolCallbackWrapper.java`
- `sport-assistant-ai/src/main/java/com/txuw/top/cloud/sport/assistant/ai/controller/AgentController.java`

下一个 LLM 不应把注意力放在：

- 原有节点如何拆分
- 原有状态如何存
- 原有记忆如何写回

下一个 LLM 应把注意力放在：

- 前端如何调用
- 前端如何接事件
- 前端收到什么字段
- 前端在哪些事件上做 UI 更新

---

## 4. 前后端交互总览

## 4.1 推荐目标：保持当前两段式交互不变

为了减少前端适配成本，**推荐继续保持当前两段式模式**，不要改成单请求直出 SSE。

推荐前端交互流程如下：

1. 前端先建立 SSE 连接
2. 后端返回 `connected` 事件
3. 前端发起一次流式咨询请求
4. 后端立即返回 `requestId`
5. 后端通过已建立的 SSE 连接持续推送本次 `requestId` 对应的事件
6. 前端根据 `requestId` 关联本轮消息流
7. 页面卸载或用户离开时，前端主动断开 SSE

这套模型对前端最友好，因为前端基本不需要改调用方式。

## 4.2 不推荐目标：改成单请求直出 SSE

虽然从 LangGraph 实现角度，单请求直出 SSE 更简单，但它会增加前端适配成本：

- 前端请求模型会变
- 前端连接管理方式会变
- 前端当前“先连接、再发请求”的流程会失效
- 前端当前基于 `requestId` 管理流式状态的代码很可能要重写

因此：

- **如无必要，不要改成单请求直出 SSE**
- **优先保留当前两段式交互**

## 4.3 前端最小消费流程

推荐前端继续按下面的逻辑工作：

```text
1. 页面进入时，调用 GET /agent/sse/connect
2. 收到 connected 事件后，保存 connectionId
3. 用户发送消息时，调用 POST /agent/plan/consult/stream
4. 从接口返回值中拿到 requestId
5. SSE 监听器持续接收事件
6. 对于 requestId 匹配当前请求的事件，更新当前消息气泡/状态
7. 收到 ai_done 或 ai_error 后，将当前请求标记为结束
8. 页面离开时，调用 DELETE /agent/sse/disconnect?connectionId=...
```

前端最小处理原则：

- `connected`：用于连接 ready 和获取 `connectionId`
- `heartbeat`：通常忽略 UI，只用于保活
- `plan_status`：更新“处理中”文案
- `ai_token`：拼接到当前答案
- `tool_*`：可选展示为工具进度
- `ai_done`：结束当前流式回答
- `ai_error`：结束当前流式回答并展示错误

---

## 5. 前端接口契约

## 5.1 建立 SSE 连接

### 接口

- 方法：`GET`
- 路径：`/agent/sse/connect`
- `Content-Type`：`text/event-stream`

### 请求参数

Query 参数：

- `sessionId`：可选
- `pageId`：可选

Header：

- `Last-Event-ID`：可选

### 语义

前端调用该接口后：

- 建立一条长期 SSE 连接
- 后端应尽快回推一个 `connected` 事件

### 推荐保持兼容的点

- 路径不改
- 参数名不改
- `Last-Event-ID` 继续接收
- 返回类型继续是 SSE

## 5.2 发起流式咨询

### 接口

- 方法：`POST`
- 路径：`/agent/plan/consult/stream`
- `Content-Type`：`application/json`

### 请求体

当前前端可继续按以下结构发送：

```json
{
  "message": "用户消息",
  "requestId": "可选，若不传则后端生成",
  "sessionId": "会话ID"
}
```

### 返回

推荐继续保持当前语义：

- 接口本身不直接返回流
- 接口立即返回本次请求的 `requestId`
- 实际内容通过 SSE 推送

如果当前前端依赖统一响应包裹结构，例如：

```json
{
  "code": 200,
  "msg": "success",
  "data": "request-id"
}
```

那么建议继续保持现有包裹结构，不要改。

### 语义

这个接口只是“启动一次流式运行”。

前端调用后：

- 立即拿到 `requestId`
- 之后所有属于本次流式运行的事件，都通过 SSE 到达

## 5.3 主动断开 SSE

### 接口

- 方法：`DELETE`
- 路径：`/agent/sse/disconnect`

### 请求参数

Query 参数：

- `connectionId`

### 语义

前端页面卸载或手动关闭时：

- 调用该接口主动断开连接

### 推荐保持兼容的点

- 路径不改
- 参数名不改
- `connectionId` 仍然从 `connected` 事件中获取

---

## 6. 前端必须理解的交互语义

## 6.1 `sessionId` 语义

- `sessionId` 表示会话
- 同一个会话下可以发起多次流式请求
- SSE 连接可以绑定到某个 `sessionId`

## 6.2 `requestId` 语义

- `requestId` 表示一次具体的流式请求
- 前端应使用 `requestId` 区分不同轮次的输出
- 除 `connected`、`heartbeat` 外，业务事件应尽量带 `requestId`

## 6.3 `connectionId` 语义

- `connectionId` 表示一条 SSE 连接
- 仅用于连接管理
- 前端主要在断开连接时使用

## 6.4 `traceId` 语义

- `traceId` 用于链路追踪
- 前端一般不直接参与业务逻辑判断
- 但这个字段仍建议继续透出，避免影响已有日志/观测联动

## 6.5 事件作用域

为了避免前端误处理事件，建议明确区分两类事件：

### 连接级事件

- `connected`
- `heartbeat`

特点：

- 与某条 SSE 连接有关
- 不一定属于某一次具体请求
- 前端通常不应把它们渲染到聊天正文里

### 请求级事件

- `plan_status`
- `ai_token`
- `ai_done`
- `ai_error`
- `tool_start`
- `tool_done`
- `tool_error`

特点：

- 应尽量带 `requestId`
- 前端应按 `requestId` 归属到某一轮请求
- 即使同一 `sessionId` 下并发多次请求，也不应串流

---

## 7. SSE 传输规范

前端收到的不是纯文本 token，而是标准 SSE frame。

每个 SSE frame 应保持如下结构：

```text
event: <eventType>
id: <eventId>
retry: 3000
data: <AgentStreamEvent JSON>
```

其中：

- `event`：等于 `AgentStreamEvent.eventType`
- `id`：等于 `AgentStreamEvent.eventId`
- `retry`：建议继续保持 `3000`
- `data`：是整个 `AgentStreamEvent` 的 JSON 字符串，不是单独的 `content`

这点非常重要：

**前端当前消费的是“整个 `AgentStreamEvent` JSON”，不是单独消费 token 文本。**

---

## 8. AgentStreamEvent 协议定义

## 8.1 字段总表

| 字段 | 类型 | 是否下发给前端 | 说明 |
| --- | --- | --- | --- |
| `userId` | `String` | 否 | 仅服务端路由使用 |
| `sessionId` | `String` | 是 | 会话标识 |
| `requestId` | `String` | 是 | 单次请求标识 |
| `traceId` | `String` | 是 | 链路追踪标识 |
| `eventType` | `String` | 是 | 事件类型 |
| `eventId` | `String` | 是 | 事件唯一 ID |
| `seq` | `Long` | 是 | 业务序号，不是所有事件都必须有 |
| `content` | `String` | 是 | 事件内容 |
| `done` | `Boolean` | 是 | 是否结束 |
| `finishReason` | `String` | 是 | 结束原因 |
| `code` | `String` | 是 | 状态码或错误码 |
| `message` | `String` | 是 | 人类可读说明 |
| `retriable` | `Boolean` | 是 | 是否可重试 |

## 8.2 硬兼容约束

以下内容建议视为硬兼容约束：

- `userId` 不应下发给前端
- `data` 必须是完整的 `AgentStreamEvent` JSON
- `eventType` 名称不要改
- `content` 的类型语义不要改
- `ai_done` 与 `ai_error` 的终态语义不要改

## 8.3 软兼容约束

以下内容若实现上需要调整，可以调整，但最好尽量保持：

- `eventId` 的具体拼接格式
- `seq` 的具体分配方式
- `message` 的具体文案
- `connected` 事件里 `serverTime` 的格式细节
- `heartbeat` 的精确发送间隔

---

## 9. 前端事件总表

## 9.1 必须兼容的事件类型

前端当前应继续能够收到以下事件：

- `connected`
- `heartbeat`
- `plan_status`
- `ai_token`
- `ai_done`
- `ai_error`
- `tool_start`
- `tool_done`
- `tool_error`

## 9.2 事件一览

| `eventType` | 用途 | `content` 格式 | 是否终态 |
| --- | --- | --- | --- |
| `connected` | 连接建立成功 | JSON 字符串 | 否 |
| `heartbeat` | 保活 | JSON 字符串 | 否 |
| `plan_status` | 流程状态提示 | 纯文本 | 否 |
| `ai_token` | 增量文本输出 | 纯文本 | 否 |
| `ai_done` | 正常结束 | 通常为空 | 是 |
| `ai_error` | 请求失败 | 通常为空，错误在 `message` | 是 |
| `tool_start` | 工具调用开始 | JSON 字符串 | 否 |
| `tool_done` | 工具调用完成 | JSON 字符串 | 否 |
| `tool_error` | 工具调用失败 | JSON 字符串 | 否 |

---

## 10. 每种事件的前端契约

## 10.1 `connected`

### 触发时机

- SSE 连接成功建立后立即发送

### 作用

- 告诉前端连接已成功建立
- 告诉前端本次连接的 `connectionId`
- 回显 `lastEventId`

### `content` 结构

```json
{
  "connectionId": "conn-xxx",
  "userId": "u123",
  "sessionId": "s123",
  "pageId": "p123",
  "serverTime": "2026-03-28T12:00:00Z",
  "lastEventId": "..."
}
```

### 前端依赖点

- 从中提取 `connectionId`
- 可据此判断连接是否 ready

### 兼容建议

- `content` 继续保持 JSON 字符串
- `connectionId` 字段名不要改

## 10.2 `heartbeat`

### 触发时机

- 连接存活期间周期性发送

### 作用

- 保活
- 让前端或浏览器中间层不误判连接断开

### `content` 结构

```json
{
  "ts": "2026-03-28T12:00:00Z"
}
```

### 兼容建议

- 继续发送
- 继续保持 JSON 字符串
- 默认建议先保持接近当前节奏

## 10.3 `plan_status`

### 触发时机

- 某次流式请求开始后
- 在后端认为需要给前端展示“过程状态”时发送

### 作用

- 驱动前端“正在分析/正在准备/正在查询”这类中间态展示

### 字段约定

- `eventType = "plan_status"`
- `content` 为纯文本
- `message` 为纯文本
- `content` 与 `message` 建议保持一致
- `done = false`

### `code` 兼容建议

如果前端已经依赖以下状态码，建议继续保留：

- `REQUEST_ACCEPTED`
- `INTENT_RESOLVED`
- `TOOLS_PREPARED`

但这里的关键是：

- 这些 code 是**前端兼容层语义**
- 不是要求你内部继续保留旧节点

例如：

- 只要内部完成了“请求已受理”，就可以发 `REQUEST_ACCEPTED`
- 只要内部完成了“问题分类/意图识别”，就可以发 `INTENT_RESOLVED`
- 只要内部完成了“工具或数据查询准备”，就可以发 `TOOLS_PREPARED`

前端关心的是语义，不关心你内部到底叫哪个节点。

## 10.4 `ai_token`

### 触发时机

- 本轮回答生成过程中，每产生一个增量文本片段时发送

### 作用

- 驱动前端逐字/逐段渲染 AI 回答

### 字段约定

- `content` 为纯文本 chunk
- `done = false`
- 推荐继续给 `seq`

### 最关键的兼容要求

- 不要重复发完整答案
- 不要同时发 token 和重复的最终全文
- 前端应只把它当作“增量文本”

## 10.5 `ai_done`

### 触发时机

- 本轮流式请求正常结束时发送一次

### 作用

- 通知前端本轮生成已正常结束

### 字段约定

- `eventType = "ai_done"`
- `done = true`
- `finishReason` 建议继续保留，例如 `stop`

### 终态规则

- 一次请求只发一次
- 发了 `ai_done` 就不应再发 `ai_error`

## 10.6 `ai_error`

### 触发时机

- 本轮流式请求失败时发送一次

### 作用

- 通知前端本轮生成失败

### 字段约定

- `eventType = "ai_error"`
- `code` 建议继续保留 `AI_STREAM_ERROR`
- `message` 放错误信息
- `retriable` 标识是否可重试

### 终态规则

- 一次请求只发一次
- 发了 `ai_error` 就不应再发 `ai_done`

## 10.7 `tool_start` / `tool_done` / `tool_error`

### 触发时机

- 后端内部工具调用开始/结束/失败时发送

### 作用

- 给前端展示“正在调用数据能力/正在查询工具”的过程信息

### `content` 结构

开始/完成：

```json
{
  "toolName": "tool-name",
  "phase": "start"
}
```

失败：

```json
{
  "toolName": "tool-name",
  "phase": "error",
  "errorMessage": "xxx"
}
```

### 兼容建议

- `content` 继续保持 JSON 字符串
- `toolName` 字段名不要改
- `phase` 字段名不要改

---

## 11. 前端视角下的事件顺序

## 11.1 正常场景

推荐保持以下顺序：

```text
connected
plan_status(REQUEST_ACCEPTED)
plan_status(...)
tool_start / tool_done      # 可选，可多次
ai_token ... ai_token
ai_done
```

## 11.2 异常场景

推荐保持以下顺序：

```text
connected
plan_status(REQUEST_ACCEPTED)
plan_status(...)            # 可选
tool_start                  # 可选
tool_error                  # 可选
ai_error
```

## 11.3 允许的插入事件

以下事件可以穿插出现：

- `heartbeat`
- 多个 `plan_status`
- 多组 `tool_start/tool_done`

## 11.4 前端真正应该依赖的顺序规则

前端真正应该依赖的，不是所有中间事件的精确顺序，而是：

1. 连接成功后会有 `connected`
2. 一次请求启动后，最终一定会以 `ai_done` 或 `ai_error` 结束
3. `ai_token` 是正文增量
4. `plan_status` 是过程提示
5. `tool_*` 是工具生命周期提示

---

## 12. LangGraph 实现时的兼容策略

## 12.1 总体策略

LangGraph 内部实现可以完全重写，但对外应保留一层兼容适配器：

- LangGraph 内部原始事件
- `AgentStreamEvent` 前端协议事件

中间必须有映射层，不能把 LangGraph 原生事件直接暴露给前端。

## 12.2 LangGraph 服务内部建议分层

推荐分成三层：

1. `Stream Gateway`
   - 管理 SSE 连接
   - 发送 `connected`
   - 发送 `heartbeat`
   - 断连清理
2. `Event Adapter`
   - 把 LangGraph 内部流式信号转换为 `AgentStreamEvent`
3. `Graph Runtime`
   - 真正执行 LangGraph 业务逻辑

## 12.3 `connected` / `heartbeat` 的位置

这两个事件应由连接层负责，不应由图节点负责。

正确做法：

- 建连成功后发送 `connected`
- 独立定时器发送 `heartbeat`

错误做法：

- 把 `connected` 做成图节点
- 把 `heartbeat` 做成图节点
- 把心跳依赖到某次 run 生命周期

## 12.4 `plan_status` 的位置

`plan_status` 是前端兼容层事件。

它可以由：

- LangGraph 某个节点完成后触发
- LangGraph 中间阶段完成后触发
- Event Adapter 根据内部阶段统一生成

重点不是谁触发，而是：

- 前端还能收到相同语义的状态提示

## 12.5 `ai_token` 的位置

建议把 LangGraph 的 token 流映射为 `ai_token`。

无论内部来自：

- `messages`
- model token stream
- custom stream

对前端都统一输出为：

- `eventType = ai_token`
- `content = chunk`

## 12.6 `tool_*` 的位置

工具调用开始/结束/失败时：

- 由内部工具包装层或 Event Adapter 发出
- 不要求沿用 Spring AI Alibaba 原有包装实现
- 但对前端仍应保持 `tool_start/tool_done/tool_error`

---

## 13. 不应强行兼容的内容

为了避免把后端重新绑死在旧实现上，以下内容不建议作为强兼容目标：

- 旧 Graph 的节点名称
- 旧 Graph 的节点数量
- 旧 Graph 的状态结构
- 旧 Graph 的保存器实现
- 旧 Graph 的工具选择逻辑
- 旧 Graph 的记忆写回方式
- 旧 `eventId` 的拼接细节
- 旧 `seq` 的绝对分配策略

这些内容可以变，只要前端协议不变。

---

## 14. 对前端最敏感的兼容点

如果只看“减少前端适配成本”，最敏感的是下面这些点：

## 14.1 接口不变

- `GET /agent/sse/connect`
- `POST /agent/plan/consult/stream`
- `DELETE /agent/sse/disconnect`

## 14.2 参数名不变

- `sessionId`
- `pageId`
- `requestId`
- `connectionId`

## 14.3 事件名不变

- `connected`
- `heartbeat`
- `plan_status`
- `ai_token`
- `ai_done`
- `ai_error`
- `tool_start`
- `tool_done`
- `tool_error`

## 14.4 `data` 结构不变

- SSE 的 `data` 继续是整个 `AgentStreamEvent` JSON

## 14.5 `content` 的类型语义不变

- `connected` / `heartbeat` / `tool_*`：JSON 字符串
- `plan_status` / `ai_token`：纯文本

## 14.6 终态规则不变

- 正常结束发 `ai_done`
- 异常结束发 `ai_error`
- 两者互斥

---

## 15. 推荐的实现优先级

下一个 LLM 实现时，建议按这个优先级推进：

1. 先保留前端接口不变
2. 再保留 SSE 事件结构不变
3. 再保留事件类型不变
4. 再保留终态语义不变
5. 最后才考虑内部业务实现如何最优

换句话说：

**先做前端兼容层，再做 LangGraph 内部优化。**

---

## 16. 交付给下一个 LLM 的明确实现要求

如果你是下一个负责实现的 LLM，请优先遵守以下要求：

1. 不要尝试迁移 Spring AI Alibaba 的原业务图实现
2. 不要把旧节点结构当成必须复刻的目标
3. 要把重点放在前端接口兼容
4. 要把重点放在 `AgentStreamEvent` 协议兼容
5. 要默认保留当前两段式交互模式
6. 要默认保留所有现有事件类型
7. 要默认保留 `connected -> request started -> token/status/tool events -> done/error` 这套前端认知模型

---

## 17. 最终建议

从“减少前端适配成本”这个目标出发，最优策略是：

- **保留当前接口形态**
- **保留当前 SSE 消费方式**
- **保留 `AgentStreamEvent` 结构**
- **保留当前事件类型与终态语义**
- **允许 LangGraph 内部业务逻辑彻底重写**

一句话总结：

**内部可以重写，前端契约尽量不动。**
