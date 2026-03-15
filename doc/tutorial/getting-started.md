# OverMindAgent 新手上手

这是一份给第一次接触这个项目的开发者准备的快速教程。目标很简单：先把服务跑起来，再理解当前的 Graph 和 LLM 配置链路。

## 1. 你会得到什么

跑完这份教程后，你应该能做到：

- 启动项目
- 配置一个可用的 LLM profile
- 理解 `settings.yaml + .env + 环境变量` 的配置方式
- 调用 `text-analysis` graph
- 调用 `tool-agent` graph
- 看懂当前的 GraphRuntime / GraphRunContext / ChatModel 链路
- 知道以后该从哪里扩展 graph、模型和 provider

## 2. 准备环境

项目基于 Python 3.11+，依赖推荐使用 `uv` 管理。

先复制本地配置模板：

```bash
cp .env.example .env
```

再安装依赖：

```bash
uv sync
```

如果你使用自己的虚拟环境，也可以显式指定 Python：

```bash
uv sync --python ".venv/Scripts/python.exe"
```

## 3. 先理解配置加载

当前项目配置按以下顺序加载，后者覆盖前者：

1. `settings.yaml`
2. `.env`
3. 系统环境变量

环境变量使用双下划线表示层级，例如：

```env
APP__PORT=9000
LLM__PROFILES__DEFAULT__MODEL=gpt-4o-mini
GRAPHS__TEXT_ANALYSIS__LLM_BINDINGS__ANALYSIS=structured_output
DATABASE__HOST=127.0.0.1
```

代码里统一这样读取：

- `settings.app.port`
- `settings.llm.profiles.default.model`
- `settings.graphs.text_analysis.llm_bindings.analysis`
- `settings.database.host`

如果后续你要新增配置，不需要修改 `config.py`。只要在 `settings.yaml` 增加默认值，或直接在 `.env` / 环境变量里写入对应键，然后在代码里按层级访问即可。

## 4. 配置 LLM

当前脚手架不再使用自定义 `LLMSession` / `protocol` 抽象，而是直接走 LangChain `BaseChatModel`。

你至少要补这几个配置：

```env
LLM__DEFAULT_PROFILE=default
LLM__ALIASES__STRUCTURED_OUTPUT=default
LLM__ALIASES__TOOL_CALLING=default
LLM__PROFILES__DEFAULT__API_KEY=your-api-key
LLM__PROFILES__DEFAULT__PROVIDER=openai
LLM__PROFILES__DEFAULT__MODEL=gpt-4o-mini
GRAPHS__TEXT_ANALYSIS__LLM_BINDINGS__ANALYSIS=structured_output
GRAPHS__TOOL_AGENT__LLM_BINDINGS__AGENT=tool_calling
```

如果你接的是 OpenAI 兼容服务，还可以配置：

```env
LLM__PROFILES__DEFAULT__BASE_URL=https://your-provider.example/v1
```

理解这几层关系很重要：

- `profile`：真实模型配置，例如 `default`
- `alias`：能力别名，例如 `structured_output`
- `graph binding`：graph 内部节点需要什么能力，例如 `analysis -> structured_output`

所以，node 不会直接写死 `provider` 或 `model`，而是通过运行时上下文拿模型。

## 5. 启动项目

开发模式启动：

```bash
uv run uvicorn overmindagent.main:app --reload
```

或者直接用项目入口：

```bash
uv run overmindagent
```

启动后先检查健康接口：

```bash
curl http://127.0.0.1:8000/health
```

如果你改了 `APP__PORT`，记得把上面的端口一并替换。

## 6. 先看有哪些 Graph

当前服务暴露了 graph 发现接口：

```bash
curl "http://127.0.0.1:8000/api/graphs"
```

返回结果里会包含：

- `name`
- `description`
- `input_schema`
- `output_schema`
- `stream_modes`

这表示 Graph 自己描述自己的输入输出与流式能力，而不是由 service 层写死某一种 graph。

## 7. 调用内置 Graph

非流式调用：

```bash
curl -X POST "http://127.0.0.1:8000/api/graphs/text-analysis/invoke" -H "Content-Type: application/json" -d '{"text":"LangGraph is useful for workflow orchestration.","session_id":"demo-1"}'
```

你会拿到一个结构化结果，包含：

- `normalized_text`
- `analysis.language`
- `analysis.summary`
- `analysis.intent`
- `analysis.sentiment`
- `analysis.categories`
- `analysis.confidence`

流式调用：

```bash
curl -N -X POST "http://127.0.0.1:8000/api/graphs/text-analysis/stream" -H "Content-Type: application/json" -d '{"text":"LangGraph is useful for workflow orchestration.","session_id":"demo-1"}'
```

流式接口当前会返回 SSE 事件，例如：

- `session`
- `updates`
- `messages`
- `result`
- `completed`
- `error`

理解方式是：

- `updates`：LangGraph 节点更新了 state
- `messages`：底层 ChatModel 正在流式吐 token / message chunk
- `result`：Graph 的最终输出

再试一个工具型 graph：

```bash
curl -X POST "http://127.0.0.1:8000/api/graphs/tool-agent/invoke" -H "Content-Type: application/json" -d '{"query":"What is the weather in Shanghai?","session_id":"demo-tool-1"}'
```

你会拿到：

- `answer`
- `tools_used[].tool_name`
- `tools_used[].tool_args`
- `tools_used[].result`

## 8. 看懂代码应该从哪里开始

如果你是第一次读这个项目，建议按这个顺序看：

1. `src/overmindagent/main.py`
2. `src/overmindagent/api/routes/graphs.py`
3. `src/overmindagent/services/graph_service.py`
4. `src/overmindagent/graphs/registry.py`
5. `src/overmindagent/graphs/runtime.py`
6. `src/overmindagent/graphs/text_analysis.py`
7. `src/overmindagent/graphs/tool_agent/`
8. `src/overmindagent/nodes/text_analysis.py`
9. `src/overmindagent/llm/`

理解顺序是：

- API 接到请求
- Service 根据 graph metadata 做输入校验、invoke 或 stream
- Graph 通过 `context_schema` 拿到 `GraphRunContext`
- Node 通过 `runtime.context.structured_model(...)` 获取模型
- `LLMRouter` 根据 profile / alias / binding 产出具体 `BaseChatModel`

## 9. 以后常见的三种扩展

新增一个 Graph：

1. 定义新的 schema
2. 定义新的 state
3. 写 node
4. 写 graph builder
5. 产出 `GraphRuntime`
6. 注册到 `graphs/registry.py`

新增一个模型配置：

1. 在 `settings.yaml` 或 `.env` 中新增 profile
2. 按需要新增 alias
3. 在 graph 的 `llm_bindings` 中切换绑定

新增一个 provider：

1. 在 `src/overmindagent/llm/factory.py` 增加 builder
2. 让 builder 返回对应的 `BaseChatModel`
3. 补 provider 对应测试

## 10. 什么时候看更完整的设计文档

当你需要理解这些内容时，再去看 Wiki 文档：

- 为什么使用 `GraphRunContext`
- profile / alias / graph binding 如何配合
- GraphRuntime、Service、Route 的职责边界
- LangGraph `v2` stream 如何映射到 SSE

对应文档在：

- `doc/wiki/scaffold.md`

## 11. 验证改动

做完代码修改后，至少跑一次：

```bash
uv run pytest -q
```

如果你改了模型路由或 graph 流式行为，建议额外验证：

- `GET /api/graphs`
- 非流式 `invoke`
- 流式 `stream`
- `with_structured_output(...)`
- `bind_tools(...)`
- OpenAI 兼容 `base_url`
