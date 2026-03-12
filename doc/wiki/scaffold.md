# OverMindAgent 脚手架说明

本文档整理当前项目的整体结构、运行方式与扩展约定，作为继续扩展 Graph、LLM Provider 和 API 的统一参考。

## 1. 项目定位

OverMindAgent 是一个基于 `FastAPI + LangGraph` 的服务端项目，重点提供：

- 清晰的 `src/` 分层结构
- 配置驱动的运行方式
- 统一的 Graph 调用入口
- 可切换 Provider / Protocol 的 LLM 统一会话层

当前默认内置一个 `text-analysis` 示例图，用来演示：

- Graph / Node / State 的职责划分
- 统一 `LLMSession` 抽象
- 结构化输出
- 非流式与流式 API 出口

## 2. 目录结构

```text
.
├─ doc/
│  ├─ tutorial/
│  │  └─ getting-started.md
│  └─ wiki/
│     └─ scaffold.md
├─ src/overmindagent/api      # FastAPI 路由与依赖注入
├─ src/overmindagent/common   # 配置与公共基础设施
├─ src/overmindagent/graphs   # Graph builder / registry / state
├─ src/overmindagent/llm      # 统一 LLM 会话层与 provider adapter
├─ src/overmindagent/nodes    # Graph 节点
├─ src/overmindagent/schemas  # Pydantic 输入输出模型
├─ src/overmindagent/services # Graph 服务编排
└─ tests                      # 测试
```

## 3. LLM 架构

当前 LLM 层不再直接暴露某个 SDK 的模型对象，而是统一收敛到会话接口：

- `LLMSession`
  - `invoke(request)` 用于非流式调用
  - `stream(request)` 用于流式事件输出
- `LLMRequest`
  - 统一描述消息、结构化输出、tools、provider 扩展参数
- `LLMResponse`
  - 统一描述文本、结构化结果、tool calls、usage
- `LLMEvent`
  - 统一描述流式事件，例如 `text_delta`、`tool_call`、`completed`

`LLMSessionFactory` 根据配置动态创建 session：

- `OVERMIND_LLM_PROVIDER`
- `OVERMIND_LLM_PROTOCOL`
- `OVERMIND_LLM_MODEL`
- `OVERMIND_LLM_BASE_URL`

当前已实现：

- `openai + responses`
- `openai + chat`

后续新增 Provider 时，原则上只需要：

1. 新增一个 adapter
2. 在 factory 中注册
3. 补测试

业务层不应该直接依赖某个 SDK 的原始对象。

## 4. 配置说明

统一使用 `.env`，前缀为 `OVERMIND_`。

### 应用配置

- `OVERMIND_APP_NAME`
- `OVERMIND_APP_ENV`
- `OVERMIND_HOST`
- `OVERMIND_PORT`
- `OVERMIND_RELOAD`
- `OVERMIND_LOG_LEVEL`

### LLM 配置

- `OVERMIND_LLM_API_KEY`
- `OVERMIND_LLM_PROVIDER`
- `OVERMIND_LLM_PROTOCOL`
- `OVERMIND_LLM_BASE_URL`
- `OVERMIND_LLM_MODEL`
- `OVERMIND_LLM_TEMPERATURE`
- `OVERMIND_LLM_TIMEOUT`
- `OVERMIND_LLM_MAX_TOKENS`
- `OVERMIND_LLM_STREAM_ENABLED`
- `OVERMIND_LLM_PARALLEL_TOOL_CALLS`
- `OVERMIND_LLM_MAX_TOOL_ROUNDS`

### Graph 配置

- `OVERMIND_GRAPH_DEFAULT_NAME`
- `OVERMIND_GRAPH_DEBUG`
- `OVERMIND_GRAPH_ENABLE_STRUCTURED_OUTPUT`
- `OVERMIND_GRAPH_CHECKPOINT_MODE`

### Observability 配置

- `OVERMIND_OBSERVABILITY_LOG_PAYLOADS`

## 5. API 入口

### 系统接口

- `GET /health`

### Graph 接口

- `POST /api/graphs/{graph_name}/invoke`
- `POST /api/graphs/{graph_name}/stream`

调用示例：

```bash
curl -X POST "http://127.0.0.1:8000/api/graphs/text-analysis/invoke" ^
  -H "Content-Type: application/json" ^
  -d "{\"text\":\"LangGraph is useful for workflow orchestration.\",\"session_id\":\"demo-1\"}"
```

流式调用示例：

```bash
curl -N -X POST "http://127.0.0.1:8000/api/graphs/text-analysis/stream" ^
  -H "Content-Type: application/json" ^
  -d "{\"text\":\"LangGraph is useful for workflow orchestration.\",\"session_id\":\"demo-1\"}"
```

## 6. Graph 扩展规范

新增一个 Graph 的建议步骤：

1. 在 `schemas/` 中定义输入输出模型
2. 在 `graphs/` 中定义 state
3. 在 `nodes/` 中实现节点逻辑
4. 在 `graphs/` 中新增 `*GraphBuilder`
5. 在 `graphs/registry.py` 中注册 graph runtime
6. 在 `api/routes/` 中决定是否暴露新接口

约定如下：

- Node 负责业务步骤，不直接处理 HTTP 细节
- Service 负责统一调用 graph 和流式输出编排
- Route 只负责协议层转换和异常映射
- LLM 调用统一经过 `LLMSession`

## 7. 开发与验证

安装依赖：

```bash
uv sync
```

启动开发服务：

```bash
uv run uvicorn overmindagent.main:app --reload
```

运行测试：

```bash
uv run pytest -q
```

## 8. 维护原则

- 保持 API 层轻量
- 保持 LLM 统一出口稳定
- 优先复用 SDK 已有能力，不重复造轮子
- 新增 Provider 时不修改业务节点逻辑
- 注释只用于解释协议映射、流式事件转换和非显然逻辑
