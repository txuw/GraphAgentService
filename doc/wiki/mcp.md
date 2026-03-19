# MCP 接入说明

本文整理 GraphAgentService 当前版本的 MCP 接入方式、最小可用接法，以及这次实际调试中踩到的坑。

目标不是泛泛介绍 MCP 概念，而是回答下面几个问题：

- 这个项目里 MCP 是怎么接进来的
- 一个 Graph 要支持 MCP，最少要改哪些地方
- 为什么看起来“只是多传个 `tools`”也会报错
- 什么时候该用 `tool_model()`，什么时候不能直接 `ainvoke(..., tools=...)`

## 1. 当前实现范围

当前项目里的 MCP 是“请求级动态工具解析”模式，核心约束如下：

- 只支持一种鉴权方式：转发当前 HTTP 请求里的 `Authorization: Bearer <token>`
- 只支持一种 transport：`streamable_http`
- 使用 `langchain-mcp-adapters` 的 `MultiServerMCPClient`
- 每次图执行时按当前 graph 的 `mcp_servers` 动态解析工具
- 本地工具默认始终保留
- 远端 MCP 工具默认全部放行
- 工具重名时，本地工具优先，远端重复工具会被跳过

也就是说，当前设计不是“进程启动时一次性加载全部 MCP 工具”，而是：

```text
HTTP 请求
-> GraphService 组装 GraphRequestContext
-> GraphRunContext 注入 current_user / request_headers / mcp_servers
-> node 在运行时调用 MCPToolResolver.resolve_tools(...)
-> 得到 本地工具 + 远端工具
-> 绑定到当前这次模型调用
```

## 2. 相关代码位置

MCP 相关代码主要在以下位置：

- `src/graphagentservice/mcp/models.py`
- `src/graphagentservice/mcp/headers.py`
- `src/graphagentservice/mcp/client.py`
- `src/graphagentservice/mcp/resolver.py`

请求上下文透传在这里：

- `src/graphagentservice/services/graph_service.py`
- `src/graphagentservice/api/routes/graphs.py`
- `src/graphagentservice/api/routes/chat.py`
- `src/graphagentservice/services/chat_stream_service.py`

Graph 侧的参考实现有两个：

- `src/graphagentservice/graphs/tool_agent/`
- `src/graphagentservice/graphs/plan_analyze/`

## 3. 配置方式

### 3.1 顶层 MCP 配置

`settings.yaml` 中的 MCP 配置结构如下：

```yaml
mcp:
  enabled: true
  request_timeout: 30.0
  tool_cache_ttl_seconds: 300
  connections:
    sport:
      enabled: true
      transport: streamable_http
      url: "http://api.txuw.top/mcp-servers/sport-assistant-mcp"
      headers: {}
      server_description: "体育工具 MCP"
```

字段说明：

- `enabled`：是否启用 MCP 总开关
- `request_timeout`：远端 MCP 请求超时
- `tool_cache_ttl_seconds`：工具发现结果缓存秒数
- `connections.<name>.url`：最终 MCP URL，不再拆 `base_url + endpoint`
- `connections.<name>.headers`：静态请求头
- `connections.<name>.transport`：当前只允许 `streamable_http`

### 3.2 Graph 级配置

某个 graph 是否接入 MCP，不看 node 名字，只看 `graphs.<graph>.mcp_servers`：

```yaml
graphs:
  plan-analyze:
    llm_bindings:
      analysis: tool_calling
    mcp_servers: ["sport"]
```

这里有两个关键点：

- `mcp_servers` 为空时，这个 graph 不会接远端 MCP
- 需要发起 tool call 的 binding，必须绑定到 `tool_calling`

如果 graph 会走工具循环，但 binding 还配成 `structured_output` 或普通 chat profile，就算代码写对了，模型也不一定会按工具模式工作。

## 4. 请求上下文是怎么传下去的

MCP 依赖的是“当前请求的 Authorization”，所以 graph 不能自己去碰 FastAPI `Request`，必须通过运行时上下文拿。

当前链路是：

1. 路由层读取 `request.headers`
2. 路由层读取 `request.state.current_user`
3. 路由层组装 `GraphRequestContext`
4. `GraphService` 把它注入 `GraphRunContext`
5. node 从 `runtime.context` 读取

也就是 node 内部可用这些字段：

```python
runtime.context.current_user
runtime.context.request_headers
runtime.context.mcp_tool_resolver
runtime.context.mcp_servers
```

这套设计的目的，是让 graph 保持和 Web 框架解耦。  
node 只关心“这次运行上下文里有哪些工具”，不关心它来自 FastAPI 还是别的协议层。

## 5. 一个 Graph 接入 MCP 的最小步骤

下面这套流程是当前项目里最稳的接法。

### 步骤 1：给 graph 配置 `mcp_servers`

```yaml
graphs:
  your-graph:
    llm_bindings:
      analysis: tool_calling
    mcp_servers: ["sport"]
```

### 步骤 2：如果 graph 需要工具循环，state 必须保留 `messages`

示例：

```python
from langchain_core.messages import AnyMessage
from langgraph.graph import add_messages
from typing_extensions import Annotated
from typing import TypedDict


class YourGraphState(TypedDict, total=False):
    query: str
    messages: Annotated[list[AnyMessage], add_messages]
    answer: str
```

如果没有 `messages`，`ToolNode` 和 `tools_condition` 就没有可消费的消息历史，graph 无法完成工具回路。

### 步骤 3：在 node 里运行时解析工具

```python
tools = await runtime.context.mcp_tool_resolver.resolve_tools(
    graph_name=runtime.context.graph_name,
    server_names=runtime.context.mcp_servers,
    current_user=runtime.context.current_user,
    request_headers=dict(runtime.context.request_headers),
)
```

注意：

- 这是“请求级”解析
- 返回结果已经是“本地工具 + 远端工具”
- 不需要 graph 自己再做一次本地工具合并

### 步骤 4：模型调用必须走 `tool_model(...)`

正确写法：

```python
model = runtime.context.tool_model(
    binding="analysis",
    tools=tools,
    tags=("analysis", "tool-calling"),
)
response = await model.ainvoke(messages)
```

不要写成：

```python
model = runtime.context.resolve_model(binding="analysis")
response = await model.ainvoke(messages, tools=tools)
```

后者就是这次踩坑的根因之一，详见后文。

### 步骤 5：`ToolNode` 也必须在运行时创建

正确写法：

```python
tool_node = ToolNode(tools)
result = await tool_node.ainvoke(state, runtime=runtime)
```

不要在 graph builder 初始化时写死：

```python
self._tool_node = ToolNode(self._tools)
```

原因是 MCP 工具是按请求动态解析的，不是启动时固定的。

### 步骤 6：builder 必须显式形成工具循环

最简可用结构：

```text
START -> analyze <-> tools -> END
```

对应 builder 大致是：

```python
graph.add_node("analyze", self._nodes.analyze)
graph.add_node("tools", self._nodes.tools)

graph.add_edge(START, "analyze")
graph.add_conditional_edges(
    "analyze",
    self._nodes.route_after_analyze,
    {
        "tools": "tools",
        "__end__": END,
    },
)
graph.add_edge("tools", "analyze")
```

如果只是线性 `START -> analyze -> END`，那模型即使产生了 `tool_calls`，graph 也没有机会去执行它们。

## 6. 最简参考：plan-analyze 的 MCP 写法

当前 `plan-analyze` 已经被改成最简工具回路，适合调试 MCP 链路。

它的行为是：

- 首次进入 `analyze`
- 若配置了 `mcp_servers`，则绑定工具模型
- 若模型返回 `tool_calls`，进入 `tools`
- `tools` 执行后回到 `analyze`
- 若模型不再返回 `tool_calls`，写入 `analysis` 并结束

这比一开始就上复杂 planner / analyzer 双节点更适合排查链路问题。

## 7. 这次实际踩到的坑

下面这些不是“理论风险”，而是这次已经踩过的点。

### 坑 1：把 `tools` 当成普通 `ainvoke()` 参数传进去

错误写法：

```python
model = runtime.context.resolve_model(binding="analysis")
response = await model.ainvoke(messages, tools=tools)
```

这会导致 OpenAI SDK 在序列化请求体时直接处理 `BaseTool` 对象，最终可能抛出类似错误：

```text
PydanticSerializationError: Unable to serialize unknown type: ModelMetaclass
```

原因不是 MCP 服务挂了，而是调用姿势错了。

根因是：

- `resolve_model()` 返回的是普通 chat model
- 工具调用模式应该在 LangChain 层先 `bind_tools(...)`
- 当前项目里这个入口已经封装成 `runtime.context.tool_model(...)`

结论：

- 要做 tool call，就走 `tool_model(...)`
- 不要自己给普通 `ainvoke()` 硬塞 `tools=...`

### 坑 2：Graph 结构太简单，根本没有 tool 回路

如果 builder 是：

```text
START -> analyze -> END
```

那就算模型返回了 tool call，也没有任何节点去执行它。

这类问题表面上看像“为什么模型不触发 MCP”，实际上是 graph 根本没给工具执行节点留位置。

最少也要有：

```text
START -> analyze <-> tools -> END
```

### 坑 3：静态 `ToolNode` 无法兼容请求级 MCP 工具

如果 `ToolNode` 在 `__init__` 或 builder 阶段就固定好了：

```python
self._tool_node = ToolNode(self._tools)
```

那它拿到的只是构建时的工具集，无法反映“当前请求的 Bearer Token + 当前 graph 的 mcp_servers + 当前远端工具列表”。

所以 MCP 场景下，`ToolNode` 必须在运行时临时创建。

### 坑 4：`ToolNode.ainvoke(...)` 不传 `runtime` 也会出问题

正确写法：

```python
await tool_node.ainvoke(state, runtime=runtime)
```

如果省略 `runtime=runtime`，`ToolNode` 在执行时拿不到当前 graph runtime，可能报缺少配置或上下文字段。

### 坑 5：Graph state 没有 `messages`

`ToolNode` 和 `tools_condition` 都依赖消息历史，至少需要：

- 最近一次 `AIMessage.tool_calls`
- 后续 `ToolMessage`

如果 state 里没有 `messages`，工具回路会失效。

### 坑 6：binding 不是 `tool_calling`

如果 graph 里要绑定工具，但 `llm_bindings.analysis` 还配成：

- `planning`
- `structured_output`
- 或普通 chat profile

那即使代码走了 `tool_model(...)`，最终模型能力配置也可能不符合预期。

当前推荐是：

```yaml
graphs:
  your-graph:
    llm_bindings:
      analysis: tool_calling
```

### 坑 7：node 里直接依赖 FastAPI Request

MCP 需要 Bearer Token，但不要在 node 里写：

```python
request.headers["Authorization"]
```

graph 层必须只依赖 `runtime.context.request_headers`。  
否则 graph 就会和 HTTP 框架绑死，后面走别的入口时会很难维护。

## 8. 调试建议

当一个 graph 的 MCP 链路不通时，建议按下面顺序排查：

1. 看 `settings.yaml` 里有没有 `mcp.enabled: true`
2. 看 graph 有没有配置 `mcp_servers`
3. 看 `llm_bindings` 是否指向 `tool_calling`
4. 看 state 是否包含 `messages`
5. 看 builder 是否真的有 `analyze <-> tools` 回路
6. 看 node 是否用了 `runtime.context.tool_model(...)`
7. 看 `ToolNode.ainvoke(...)` 是否传了 `runtime=runtime`
8. 看请求头里是否真的带了 `Authorization`
9. 看远端 MCP URL 是否是最终 URL，而不是拼接前半段

如果日志里出现的是序列化错误，而不是远端网络错误，优先检查“是不是把 `tools` 错传给了普通 `ainvoke()`”。

## 9. 什么时候适合先用最简 graph 调试

如果目标是“先把 MCP 链路打通”，推荐先用最简 graph：

```text
START -> analyze <-> tools -> END
```

不要一开始就引入：

- planner / executor / critic 多角色
- structured output
- 多段 prompt 重组
- 中间态 schema 太多

原因很简单：

- MCP 本身已经增加了一个远端依赖面
- Bearer 转发、工具发现、工具执行、模型工具回路，这几层任何一层都可能出问题
- graph 越复杂，越难判断问题到底出在“业务逻辑”还是“MCP 链路”

先用最简回路跑通，再往上叠复杂节点，是当前项目里最实用的调试策略。

## 10. 当前版本的一句话原则

在 GraphAgentService 里接 MCP，可以记成一句话：

```text
Graph 决定要接哪些 MCP server，
GraphService 负责把请求上下文带进来，
node 在运行时解析工具，
模型通过 tool_model() 绑定工具，
ToolNode 在运行时执行工具。
```

如果只记一条经验，那就是：

```text
不要把 tools 直接塞进普通 model.ainvoke(...)，
要么用 tool_model(...)，要么就别做工具调用。
```
