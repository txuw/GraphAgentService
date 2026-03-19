# GraphAgentService 新手上手

这是一份面向当前代码库的快速上手文档。目标很简单：

- 把服务跑起来
- 配好一个可用的 LLM
- 调通当前内置的几个 graph
- 理解 `text-analysis`、`tool-agent`、`plan-analyze` 各自适合拿来做什么
- 知道 MCP 在这个项目里是怎么接进 graph 的

如果你刚接手项目，建议先看这篇，再看：

- `doc/wiki/scaffold.md`
- `doc/wiki/runtime-binding.md`
- `doc/wiki/mcp.md`

## 1. 你会得到什么

读完并跑通本教程后，你应该能做到：

- 启动项目
- 理解 `settings.yaml + .env + 环境变量` 的配置覆盖顺序
- 配置一个可用的 OpenAI / OpenAI 兼容模型
- 调用 `text-analysis`
- 调用 `tool-agent`
- 用 `plan-analyze` 调试最简 MCP 工具回路
- 知道后续新增 graph 时应该从哪些文件开始改

## 2. 准备环境

项目基于 Python 3.11+，依赖推荐使用 `uv` 管理。

先复制本地环境变量模板：

```powershell
Copy-Item ".env.example" ".env" -Force
```

安装依赖：

```powershell
uv sync
```

如果你使用自己的虚拟环境，也可以显式指定 Python：

```powershell
uv sync --python ".venv/Scripts/python.exe"
```

## 3. 先理解配置加载顺序

当前项目的配置覆盖顺序固定为：

1. `settings.yaml`
2. `.env`
3. 系统环境变量

后者会覆盖前者。

环境变量使用双下划线表示层级，例如：

```env
APP__PORT=9000
LLM__PROFILES__DEFAULT__MODEL=gpt-4o-mini
GRAPHS__TEXT_ANALYSIS__LLM_BINDINGS__ANALYSIS=structured_output
GRAPHS__PLAN_ANALYZE__LLM_BINDINGS__ANALYSIS=tool_calling
GRAPHS__PLAN_ANALYZE__MCP_SERVERS=["sport"]
```

代码里按层级读取，例如：

- `settings.app.port`
- `settings.llm.profiles.default.model`
- `settings.graphs.text_analysis.llm_bindings.analysis`
- `settings.graphs.plan_analyze.mcp_servers`

如果你后续新增配置，一般不需要改 `config.py`。  
只要在 `settings.yaml` 增加默认值，或在 `.env` / 环境变量里覆盖，然后在代码里按层级读取即可。

## 4. 配置 LLM

当前项目直接使用 LangChain 的 `BaseChatModel`，不再维护自定义 `LLMSession` / `protocol` 抽象。

最少需要补齐这些配置：

```env
LLM__DEFAULT_PROFILE=default
LLM__ALIASES__STRUCTURED_OUTPUT=default
LLM__ALIASES__TOOL_CALLING=default
LLM__PROFILES__DEFAULT__API_KEY=your-api-key
LLM__PROFILES__DEFAULT__PROVIDER=openai
LLM__PROFILES__DEFAULT__MODEL=gpt-4o-mini
```

如果你接的是 OpenAI 兼容服务，还可以配置：

```env
LLM__PROFILES__DEFAULT__BASE_URL=https://your-provider.example/v1
```

这里需要理解三层概念：

- `profile`：真实模型配置，例如 `default`
- `alias`：能力别名，例如 `structured_output`、`tool_calling`
- `graph binding`：graph 内部某个 node 需要的能力，例如 `analysis -> tool_calling`

也就是说，node 本身不会直接硬编码模型名，而是通过 `runtime.context` 解析到真正的 chat model。

## 5. 当前内置 graph 简介

当前项目里最常用的三个 graph 是：

### `text-analysis`

适合验证结构化输出链路，特点是：

- 走 `structured_model(...)`
- 输出结构化分析结果
- 不涉及 MCP

### `tool-agent`

适合验证标准工具调用回路，特点是：

- `prepare -> agent <-> tools -> finalize`
- 默认始终保留本地工具
- 如果配置了 `mcp_servers`，会把远端 MCP 工具一并合并进来

### `plan-analyze`

当前已经被调整成最简 MCP 调试 graph，特点是：

- 使用最简回路：`START -> analyze <-> tools -> END`
- `analyze` 会按请求动态绑定 MCP 工具
- 适合先把 Bearer 转发、工具发现、工具执行链路跑通

它现在不是“先 planner 再 analyzer”的复杂示例，而是用来调试 MCP 最短闭环。

## 6. 配置 MCP

当前 MCP 的最小配置示例在 `settings.yaml` 里已经给出，结构大致如下：

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

某个 graph 是否接入 MCP，看的是：

```yaml
graphs:
  plan-analyze:
    llm_bindings:
      analysis: tool_calling
    mcp_servers: ["sport"]
```

这里有两个关键点：

- `mcp_servers` 决定这个 graph 会接哪些远端 MCP
- 要做工具调用的 binding，必须指向 `tool_calling`

更完整的说明见：

- `doc/wiki/mcp.md`

## 7. 启动项目

开发模式：

```powershell
uv run uvicorn graphagentservice.main:app --reload
```

或直接通过项目入口：

```powershell
uv run graphagentservice
```

启动后先检查健康接口：

```powershell
curl "http://127.0.0.1:8000/health"
```

如果你改了 `APP__PORT`，记得把下面示例中的端口一起替换。

## 8. 先看有哪些 Graph

当前服务暴露了 graph 发现接口：

```powershell
curl "http://127.0.0.1:8000/api/graphs"
```

如果 Logto 开启了认证，需要额外带上：

```powershell
-H "Authorization: Bearer <token>"
```

返回结果里会包含：

- `name`
- `description`
- `input_schema`
- `output_schema`
- `stream_modes`

这表示每个 graph 都能描述自己的输入输出与流式能力，而不是由 route 或 service 写死。

## 9. 调用 `text-analysis`

非流式调用：

```powershell
curl -X POST "http://127.0.0.1:8000/api/graphs/text-analysis/invoke" `
  -H "Content-Type: application/json" `
  -d "{\"text\":\"LangGraph is useful for workflow orchestration.\",\"session_id\":\"demo-text-1\"}"
```

它的 `data` 里会包含：

- `normalized_text`
- `analysis.language`
- `analysis.summary`
- `analysis.intent`
- `analysis.sentiment`
- `analysis.categories`
- `analysis.confidence`

流式调用：

```powershell
curl -N -X POST "http://127.0.0.1:8000/api/graphs/text-analysis/stream" `
  -H "Content-Type: application/json" `
  -d "{\"text\":\"LangGraph is useful for workflow orchestration.\",\"session_id\":\"demo-text-1\"}"
```

当前 `/stream` 会输出这些 SSE 事件：

- `session`
- `updates`
- `messages`
- `result`
- `completed`
- `error`

可以这样理解：

- `updates`：LangGraph 节点更新了 state
- `messages`：底层模型正在产生 message chunk
- `result`：graph 最终输出

## 10. 调用 `tool-agent`

调用示例：

```powershell
curl -X POST "http://127.0.0.1:8000/api/graphs/tool-agent/invoke" `
  -H "Content-Type: application/json" `
  -d "{\"query\":\"What is the weather in Shanghai?\",\"session_id\":\"demo-tool-1\"}"
```

`tool-agent` 的 `data` 里通常会拿到：

- `answer`
- `tools_used[].tool_name`
- `tools_used[].tool_args`
- `tools_used[].result`

当前它的工具来源是：

- 本地默认工具：`src/graphagentservice/tools/`
- 如果配置了 `mcp_servers`，还会动态合并远端 MCP 工具

工具冲突规则是：

- 本地工具优先
- 远端重名工具跳过
- 多个远端重名时保留先加入的那个

## 11. 用 `plan-analyze` 调试 MCP

如果你的目标是“先把 MCP 链路跑通”，建议优先调这个 graph。

调用示例：

```powershell
curl -X POST "http://127.0.0.1:8000/api/graphs/plan-analyze/invoke" `
  -H "Content-Type: application/json" `
  -H "Authorization: Bearer <token>" `
  -d "{\"query\":\"你好\",\"session_id\":\"demo-plan-1\"}"
```

为什么这里建议显式带 Bearer：

- 当前 MCP 默认会转发当前请求的 `Authorization`
- 这样最接近真实生产链路
- 即使本地 Logto 关闭，也建议调试时把 Bearer 一并带上，方便验证远端 MCP 是否吃到了认证头

`plan-analyze` 当前用的是最简回路：

```text
START -> analyze <-> tools -> END
```

这条链路适合排查：

- `mcp_servers` 是否生效
- Bearer 是否被正确转发
- 模型是否真正进入工具调用模式
- `ToolNode` 是否执行了远端返回的工具

如果你后续要把一个新 graph 接上 MCP，建议先模仿 `plan-analyze` 的最简模式，把链路调通后再加更复杂的业务节点。

## 12. 读代码建议从哪里开始

如果你第一次读这个项目，建议按这个顺序看：

1. `src/graphagentservice/main.py`
2. `src/graphagentservice/api/routes/graphs.py`
3. `src/graphagentservice/services/graph_service.py`
4. `src/graphagentservice/graphs/registry.py`
5. `src/graphagentservice/graphs/runtime.py`
6. `src/graphagentservice/graphs/text_analysis/`
7. `src/graphagentservice/graphs/tool_agent/`
8. `src/graphagentservice/graphs/plan_analyze/`
9. `src/graphagentservice/mcp/`
10. `src/graphagentservice/llm/`

建议的理解顺序是：

- API 收到请求
- Service 校验 payload 并构造 graph 运行上下文
- Graph 根据 `GraphRuntime` 和 `GraphRunContext` 运行
- Node 通过 `runtime.context` 获取模型或工具模型
- MCP 通过 `mcp_tool_resolver` 在请求期解析远端工具

## 13. 后续常见扩展

### 新增一个 Graph

通常需要做这些事：

1. 定义 schema
2. 定义 state
3. 编写 nodes
4. 编写 builder
5. 产出 `GraphRuntime`
6. 注册到 `graphs/registry.py`

### 让一个 Graph 支持 MCP

通常需要做这些事：

1. 在 `settings.yaml` 中配置 `mcp.connections`
2. 给 `graphs.<graph>.mcp_servers` 指定服务名
3. 给会发起工具调用的 binding 配上 `tool_calling`
4. 在 node 里通过 `runtime.context.tool_model(..., tools=...)` 绑定工具
5. 如果要执行工具，builder 里必须有 `analyze <-> tools` 之类的回路

### 新增一个 provider

通常需要做这些事：

1. 在 `src/graphagentservice/llm/factory.py` 增加 builder
2. 让 builder 返回对应的 `BaseChatModel`
3. 补 provider 对应测试

## 14. 常见误区

### 误区 1：普通 `ainvoke()` 也能直接塞 `tools=...`

不要这么做。

如果要做工具调用，应该走：

```python
runtime.context.tool_model(binding="analysis", tools=tools)
```

而不是：

```python
runtime.context.resolve_model(...).ainvoke(messages, tools=tools)
```

后者很容易导致请求序列化报错。

### 误区 2：builder 里只有 `START -> analyze -> END` 也能调工具

不行。

如果 graph 没有 `tools` 节点和回边，模型即使生成了 `tool_calls`，graph 也不会执行。

### 误区 3：把 `ToolNode` 固定在构建期

MCP 是请求级动态工具解析，所以 `ToolNode` 也应该在运行时基于当前请求的工具列表创建。

## 15. 验证改动

做完代码修改后，至少跑一次：

```powershell
uv run pytest -q
```

如果你改了 MCP 或工具调用行为，建议额外验证：

- `GET /api/graphs`
- `POST /api/graphs/text-analysis/invoke`
- `POST /api/graphs/tool-agent/invoke`
- `POST /api/graphs/plan-analyze/invoke`
- `POST /api/graphs/*/stream`

## 16. 进一步阅读

如果你想继续深入理解设计细节，建议接着看：

- `doc/wiki/scaffold.md`
- `doc/wiki/runtime-binding.md`
- `doc/wiki/logto-auth.md`
- `doc/wiki/mcp.md`
