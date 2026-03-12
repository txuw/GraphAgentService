# OverMindAgent 脚手架说明

本文档整理当前脚手架的使用方式与开发规范，作为后续扩展 Agent/Graph 的统一参考。

## 1. 定位与技术栈

- 目标：提供可扩展的 FastAPI + LangGraph 服务骨架，便于新增 Graph、Node、Schema 与 API。
- 依赖管理：`uv` + `pyproject.toml`。
- 目录结构：采用 `src/` 结构并通过 `overmindagent` 作为包名入口。

## 2. 快速开始

1. 复制环境文件：

```bash
cp .env.example .env
```

2. 安装依赖：

```bash
uv sync
```

3. 启动开发服务：

```bash
uv run uvicorn overmindagent.main:app --reload
```

4. 按 `.env` 启动（使用 `overmindagent` 脚本入口）：

```bash
uv run overmindagent
```

## 3. 配置规范

- 统一使用 `.env` 配置，前缀为 `OVERMIND_`。
- 所有配置集中在 `src/overmindagent/common/config.py` 中管理。
- 配置分层：
  - `AppSettings`：应用基础配置
  - `LLMSettings`：模型配置
  - `GraphSettings`：Graph 行为配置
  - `ObservabilitySettings`：可观测性配置

### 常用配置项（来自 `.env.example`）

- 应用：`OVERMIND_APP_NAME`、`OVERMIND_APP_ENV`、`OVERMIND_HOST`、`OVERMIND_PORT`、`OVERMIND_RELOAD`、`OVERMIND_LOG_LEVEL`
- 模型：`OVERMIND_LLM_API_KEY`、`OVERMIND_LLM_BASE_URL`、`OVERMIND_LLM_MODEL`、`OVERMIND_LLM_TEMPERATURE`、`OVERMIND_LLM_TIMEOUT`、`OVERMIND_LLM_MAX_TOKENS`
- Graph：`OVERMIND_GRAPH_DEFAULT_NAME`、`OVERMIND_GRAPH_DEBUG`、`OVERMIND_GRAPH_ENABLE_STRUCTURED_OUTPUT`、`OVERMIND_GRAPH_CHECKPOINT_MODE`
- 观测：`OVERMIND_OBSERVABILITY_LOG_PAYLOADS`

注意：实际调用 LLM 需要配置 `OVERMIND_LLM_API_KEY`。

## 4. 目录结构与职责

```text
src/overmindagent/api      # FastAPI 路由与依赖
src/overmindagent/common   # 配置与共享基础设施
src/overmindagent/graphs   # LangGraph builder / registry / state
src/overmindagent/llm      # LLM 工厂与 provider 抽象
src/overmindagent/nodes    # Graph 节点
src/overmindagent/schemas  # Pydantic 输入输出模型
src/overmindagent/services # Graph 编排服务
```

### 关键模块职责

- `main.py`：FastAPI 应用入口，初始化 GraphRegistry 与 GraphService。
- `__main__.py`：命令行入口 `overmindagent`，读取配置并启动 uvicorn。
- `common/config.py`：所有运行配置统一入口。
- `graphs/registry.py`：Graph 注册与获取逻辑。
- `services/graph_service.py`：Graph 调用统一入口。
- `nodes/*`：LangGraph 节点实现。
- `schemas/*`：请求/响应与结构化输出模型。
- `api/routes/*`：API 路由层，保持轻逻辑。

## 5. API 使用方式

- 健康检查：`GET /health`
- Graph 调用：`POST /api/graphs/{graph_name}/invoke`

请求示例：

```bash
curl -X POST "http://127.0.0.1:8000/api/graphs/text-analysis/invoke" \
  -H "Content-Type: application/json" \
  -d '{"text":"LangGraph is useful for workflow orchestration.","session_id":"demo-1"}'
```

## 6. Graph 扩展规范

新增一个 Graph 的推荐步骤：

1. 定义 State
- 在 `src/overmindagent/graphs/state.py` 中新增 State（或新增独立 state 文件）。

2. 定义 Schema
- 在 `src/overmindagent/schemas/` 中新增请求与输出模型。

3. 编写 Node
- 在 `src/overmindagent/nodes/` 中新增 Node 类，方法返回 State 片段。

4. 组装 Graph
- 在 `src/overmindagent/graphs/` 中新增 `*GraphBuilder`，定义节点与边。

5. 注册 Graph
- 在 `src/overmindagent/graphs/registry.py` 中实例化并注册到 `GraphRegistry`。

6. API 层接入（如需要）
- 在 `src/overmindagent/api/routes/` 中新增或扩展路由。

### Graph 编排约定

- Node 方法必须是纯函数式风格：输入 `state`，输出 `state` 片段。
- 统一由 `GraphService.invoke()` 调用 Graph。
- Graph 名称与路由参数保持一致（例如 `text-analysis`）。

## 7. 开发规范

- 结构分层：
  - API 层只做参数校验/路由转发。
  - Service 层负责 Graph 调度。
  - Graph/Node 层负责业务流程与模型调用。
- Schema 统一使用 Pydantic，避免在 API/Node 中使用裸 dict 作为输入输出。
- 依赖统一放到 `pyproject.toml`，禁用临时 pip 安装。
- 新增 Graph 时必须同步完善 README 或文档，保持入口清晰。

## 8. 测试与部署

- 运行测试：

```bash
uv run pytest
```

- Docker 构建：

```bash
docker build -t overmindagent:local .
```

- Docker 运行：

```bash
docker run --rm -p 8000:8000 --env-file .env overmindagent:local
```

- k3s 部署：参见 `deploy/k3s/` 目录说明。
