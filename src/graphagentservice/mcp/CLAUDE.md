[根目录](../../CLAUDE.md) > [src](../) > **mcp**

# MCP 模块

> 最后更新：2026-04-04

## 模块职责

MCP（Model Context Protocol）模块负责集成和管理远端工具服务，提供：

- **MCP 客户端**：基于 `langchain-mcp-adapters` 的多服务器客户端
- **工具解析器**：运行时动态解析本地工具与远端 MCP 工具
- **请求上下文透传**：将认证信息、请求头转发给 MCP 服务器
- **工具合并策略**：处理本地工具与远端工具的冲突

## 目录结构

```
mcp/
├── __init__.py      # 模块导出
├── models.py        # MCP 配置数据模型
├── headers.py       # 请求头处理
├── client.py        # MCP 客户端工厂
└── resolver.py      # 工具解析器
```

## 核心组件

### MCPSettings (`models.py`)

**职责**：MCP 配置数据模型

**核心字段**：
```python
class MCPSettings(BaseModel):
    enabled: bool = True                              # MCP 总开关
    request_timeout: float = 30.0                     # 请求超时（秒）
    tool_cache_ttl_seconds: int = 300                 # 工具缓存 TTL
    connections: dict[str, MCPConnectionConfig]       # MCP 服务器配置
```

### MCPClientFactory (`client.py`)

**职责**：创建和管理 MCP 客户端

**核心方法**：
```python
async def get_tools_for_servers(
    self,
    server_names: Sequence[str],
    request_headers: dict[str, str],
) -> list[BaseTool]
```

**特点**：
- 支持多服务器并发工具发现
- 自动转发请求头（包括 `Authorization`）
- 内置工具缓存机制

### MCPToolResolver (`resolver.py`)

**职责**：解析和合并本地工具与远端 MCP 工具

**核心方法**：
```python
async def resolve_tools(
    self,
    *,
    graph_name: str,
    server_names: Sequence[str],
    current_user: AuthenticatedUser,
    request_headers: dict[str, str],
) -> list[BaseTool]
```

**合并策略**：
1. 本地工具优先
2. 远端重名工具跳过
3. 多个远端重名时保留先加入的

## 配置方式

### 顶层 MCP 配置

```yaml
mcp:
  enabled: true                              # MCP 总开关
  request_timeout: 30.0                      # 请求超时（秒）
  tool_cache_ttl_seconds: 300                # 工具缓存 TTL
  connections:
    sport:                                   # MCP 服务器名称
      enabled: true
      transport: streamable_http             # 当前只支持 streamable_http
      url: "http://api.txuw.top/mcp-servers/sport-assistant-mcp"
      headers: {}                            # 静态请求头
      server_description: "体育工具 MCP"
```

### Graph 级配置

```yaml
graphs:
  plan-analyze:
    llm_bindings:
      analysis: tool_calling                 # 必须是 tool_calling
    mcp_servers: ["sport"]                   # 指定使用的 MCP 服务器
```

### 环境变量

```env
# MCP 总开关
MCP__ENABLED=true

# 请求超时
MCP__REQUEST_TIMEOUT=30.0

# 工具缓存 TTL
MCP__TOOL_CACHE_TTL_SECONDS=300

# MCP 服务器配置
MCP__CONNECTIONS__SPORT__ENABLED=true
MCP__CONNECTIONS__SPORT__TRANSPORT=streamable_http
MCP__CONNECTIONS__SPORT__URL=http://api.txuw.top/mcp-servers/sport-assistant-mcp
```

## 使用方式

### 在 Graph 中使用

```python
class MyGraphNodes:
    async def analyze(
        self,
        state: MyGraphState,
        runtime: GraphRunContext,
    ) -> dict[str, object]:
        # 1. 解析 MCP 工具
        tools = await runtime.context.mcp_tool_resolver.resolve_tools(
            graph_name=runtime.context.graph_name,
            server_names=runtime.context.mcp_servers,
            current_user=runtime.context.current_user,
            request_headers=dict(runtime.context.request_headers),
        )

        # 2. 绑定工具模型
        model = runtime.context.tool_model(
            binding="analysis",
            tools=tools,
        )

        # 3. 调用模型
        response = await model.ainvoke(messages)

        return {"result": response}
```

### 在 Builder 中设置 ToolNode

```python
def build(self) -> GraphRuntime:
    graph = StateGraph(...)

    # 添加节点
    graph.add_node("analyze", self._nodes.analyze)
    graph.add_node("tools", self._nodes.tools)

    # 添加工具循环
    graph.add_conditional_edges(
        "analyze",
        self._nodes.route_after_analyze,
        {"tools": "tools", "__end__": END},
    )
    graph.add_edge("tools", "analyze")

    return GraphRuntime(...)
```

### Node 中的 tools 实现

```python
class MyGraphNodes:
    def tools(self, state: MyGraphState, runtime: GraphRunContext):
        # 运行时动态创建 ToolNode
        async def _execute_tools(state: MyGraphState):
            tools = await runtime.context.mcp_tool_resolver.resolve_tools(
                graph_name=runtime.context.graph_name,
                server_names=runtime.context.mcp_servers,
                current_user=runtime.context.current_user,
                request_headers=dict(runtime.context.request_headers),
            )
            tool_node = ToolNode(tools)
            return await tool_node.ainvoke(state, runtime=runtime)

        return _execute_tools
```

## 请求上下文透传

MCP 依赖当前请求的认证信息，透传链路：

```
HTTP Request (Authorization: Bearer <token>)
→ API Route (build_graph_request_context)
→ GraphService (构建 GraphRunContext)
→ Graph Node (runtime.context.request_headers)
→ MCPToolResolver.resolve_tools()
→ MCPClientFactory.get_tools_for_servers()
→ MCP Server (带 Authorization 头)
```

## 调试建议

### 1. 检查 MCP 配置

```bash
# 检查配置是否加载
curl "http://localhost:8000/api/graphs" | jq
```

### 2. 验证 Binding

确保 `llm_bindings` 指向 `tool_calling`：
```yaml
graphs:
  your-graph:
    llm_bindings:
      analysis: tool_calling  # 必须
```

### 3. 检查 Graph 结构

确保有工具循环：
```text
START -> analyze <-> tools -> END
```

### 4. 验证工具调用

使用 `plan-analyze` 作为最简调试 Graph：
```bash
curl -X POST "http://localhost:8000/api/graphs/plan-analyze/invoke" \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer <token>" \
  -d '{"query":"你好","session_id":"debug-1"}'
```

### 5. 开启日志

```env
APP__LOG_LEVEL=debug
OBSERVABILITY__LOG_PAYLOADS=true
```

## 常见问题

### Q: MCP 工具没有生效？

A: 检查以下几点：
1. `mcp.enabled=true`
2. Graph 配置了 `mcp_servers`
3. `llm_bindings` 指向 `tool_calling`
4. Graph 有工具循环结构
5. 使用了 `runtime.context.tool_model()` 而非普通 `ainvoke()`

### Q: 工具调用报序列化错误？

A: 确保使用正确的调用方式：
```python
# 正确
model = runtime.context.tool_model(binding="analysis", tools=tools)
await model.ainvoke(messages)

# 错误
model = runtime.context.resolve_model(binding="analysis")
await model.ainvoke(messages, tools=tools)  # 会报序列化错误
```

### Q: MCP 服务器认证失败？

A: 检查：
1. 请求是否带了 `Authorization` 头
2. `request_headers` 是否正确透传
3. MCP 服务器 URL 是否正确

### Q: 工具重名如何处理？

A: 当前策略：
- 本地工具优先
- 远端重名工具跳过
- 多个远端重名时保留先加入的

## 最佳实践

### 1. 使用最简 Graph 调试 MCP

先用 `START -> analyze <-> tools -> END` 跑通链路，再增加复杂节点。

### 2. 统一使用 tool_model()

不要直接给 `ainvoke()` 传 `tools` 参数，统一使用 `runtime.context.tool_model()`。

### 3. 运行时创建 ToolNode

不要在 `__init__` 或 builder 阶段固定 ToolNode，在运行时根据请求动态创建。

### 4. 保持 Graph 框架无关

Node 只依赖 `runtime.context`，不直接依赖 FastAPI `Request`。

## 相关文件清单

- `__init__.py` - 模块导出
- `models.py` - MCP 配置数据模型
- `headers.py` - 请求头处理
- `client.py` - MCP 客户端工厂
- `resolver.py` - 工具解析器

## 变更记录

### 2026-04-04
- 初始化 MCP 模块文档
- 补充调试指南与最佳实践
