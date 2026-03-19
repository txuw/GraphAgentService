# GraphAgentService

基于 `FastAPI + LangGraph` 的服务端脚手架，使用 `uv` 管理依赖，配置采用 `Dynaconf` 的 `settings.yaml + .env + 环境变量` 组合。

当前脚手架的 LLM 交互方式已经调整为更贴近 LangGraph / LangChain 官方实践的形态：

- Graph 使用 `StateGraph(state_schema, context_schema, input_schema, output_schema)`
- Node 通过 `Runtime[GraphRunContext]` 在运行时拿模型
- LLM 层直接产出 `BaseChatModel`
- 结构化输出走 `with_structured_output(...)`
- 工具调用走 `bind_tools(...)`
- 流式输出走 LangGraph `astream(..., version="v2")`

当前内置两个示例 graph：

- `text-analysis`：结构化输出 graph
- `tool-agent`：`ToolNode` 工具调用 graph

## 项目结构

```text
.
├── doc/
├── src/graphagentservice/api      # FastAPI 路由与依赖
├── src/graphagentservice/common   # 配置与公共基础设施
├── src/graphagentservice/graphs   # Graph builder / registry / runtime / state
├── src/graphagentservice/llm      # ChatModel profile / router / factory
├── src/graphagentservice/tools    # 可复用工具定义与注册
├── src/graphagentservice/schemas  # Pydantic 输入输出模型
├── src/graphagentservice/services # Graph 编排服务
├── tests                      # 测试
├── settings.yaml              # 默认配置
└── .env.example               # 本地覆盖示例
```

## 快速开始

先复制本地配置模板：

```bash
cp .env.example .env
```

至少补齐以下配置：

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

如果你接的是 OpenAI 兼容服务，还可以补：

```env
LLM__PROFILES__DEFAULT__BASE_URL=https://your-provider.example/v1
```

安装依赖：

```bash
uv sync
```

启动服务：

```bash
uv run graphagentservice
```

开发模式：

```bash
uv run uvicorn graphagentservice.main:app --reload
```

## 配置约定

当前配置加载顺序固定为：

1. `settings.yaml`
2. `.env`
3. 系统环境变量

环境变量使用双下划线映射层级，例如：

```env
APP__PORT=9000
LLM__PROFILES__DEFAULT__API_KEY=your-api-key
LLM__PROFILES__DEFAULT__BASE_URL=https://your-provider.example/v1
GRAPHS__TEXT_ANALYSIS__LLM_BINDINGS__ANALYSIS=structured_output
GRAPHS__TOOL_AGENT__LLM_BINDINGS__AGENT=tool_calling
DATABASE__HOST=127.0.0.1
```

代码中统一按层级访问：

- `settings.app.port`
- `settings.llm.profiles.default.model`
- `settings.graphs.text_analysis.llm_bindings.analysis`
- `settings.database.host`

当前 LLM 配置分成三层：

- `profile`：一个真实模型连接配置，例如 `provider / base_url / model / api_key`
- `alias`：一个能力别名，例如 `structured_output`、`tool_calling`
- `graph binding`：某个 graph 内部节点绑定哪个 alias 或 profile

例如当前默认配置里：

- `structured_output -> default`
- `text-analysis.analysis -> structured_output`
- `tool-agent.agent -> tool_calling`

最终 node 里只声明“我要 `analysis` 这个 binding 的结构化模型”，不直接依赖 `provider` 或具体模型名。

## 当前脚手架的 Graph / LLM 关系

当前默认内置两个示例 graph：

- `text-analysis`：演示 `with_structured_output(...)`
- `tool-agent`：演示 `bind_tools(...) + ToolNode + tools_condition`

其中 `text-analysis` 用于演示：

- Graph / Node / State / Runtime 分层
- Graph 通过 `context_schema` 注入运行期依赖
- Node 通过 `runtime.context.structured_model(...)` 获取模型
- 基于 `Pydantic` 的结构化输出
- LangGraph `v2` 流式事件转 SSE

`tool-agent` 则展示：

- 顶层 `tools/` 复用工具模块与注册入口
- 模型节点与工具节点循环编排
- tool trace 输出

关键对象：

- `LLMProfile`：标准化后的模型配置
- `LLMRouter`：解析 profile / alias，并产出 `BaseChatModel`
- `GraphRunContext`：Graph 运行时上下文，向 node 暴露 `resolve_model()`、`structured_model()`、`tool_model()`
- `GraphRuntime`：Graph 的元信息与可执行对象

## 调用示例

先查看可用 graph：

```bash
curl "http://127.0.0.1:8000/api/graphs"
```

非流式调用：

```bash
curl -X POST "http://127.0.0.1:8000/api/graphs/text-analysis/invoke" -H "Content-Type: application/json" -d '{"text":"LangGraph is useful for workflow orchestration.","session_id":"demo-1"}'
```

流式调用：

```bash
curl -N -X POST "http://127.0.0.1:8000/api/graphs/text-analysis/stream" -H "Content-Type: application/json" -d '{"text":"LangGraph is useful for workflow orchestration.","session_id":"demo-1"}'
```

`tool-agent` 调用示例：

```bash
curl -X POST "http://127.0.0.1:8000/api/graphs/tool-agent/invoke" -H "Content-Type: application/json" -d '{"query":"What is the weather in Shanghai?","session_id":"demo-tool-1"}'
```

`/stream` 当前会返回 SSE 事件，例如：

- `session`
- `updates`
- `messages`
- `result`
- `completed`
- `error`

其中：

- `updates` 对应 graph 节点的状态更新
- `messages` 对应底层 ChatModel 的流式 token / message chunk
- `result` 是 graph 最终输出模型

## 测试

```bash
uv run pytest -q
```

## 文档

- 脚手架说明：`doc/wiki/scaffold.md`
- Logto 鉴权：`doc/wiki/logto-auth.md`
- 新手上手：`doc/tutorial/getting-started.md`

## Docker

构建镜像：

```bash
docker build -t graphagentservice:local .
```

通过 `.env` 注入配置运行：

```bash
docker run --rm -p 8000:8000 --env-file .env graphagentservice:local
```

镜像内默认只包含 `/app/settings.yaml`。如果要通过文件覆盖配置，请挂载到 `/app/.env` 或 `/app/settings.yaml`；单纯挂载到 `/config` 不会自动生效。

## Kubernetes / k3s

推荐使用 `ConfigMap + Secret` 直接注入环境变量，不需要额外挂载配置目录：

```yaml
envFrom:
  - configMapRef:
      name: graph-agent-service-config
  - secretRef:
      name: graph-agent-service-secret
```

一个最小示例：

```yaml
apiVersion: v1
kind: ConfigMap
metadata:
  name: graph-agent-service-config
data:
  APP__PORT: "8000"
  LLM__DEFAULT_PROFILE: default
  LLM__ALIASES__STRUCTURED_OUTPUT: default
  LLM__ALIASES__TOOL_CALLING: default
  LLM__PROFILES__DEFAULT__PROVIDER: openai
  LLM__PROFILES__DEFAULT__MODEL: gpt-4o-mini
  GRAPHS__TEXT_ANALYSIS__LLM_BINDINGS__ANALYSIS: structured_output
  GRAPHS__TOOL_AGENT__LLM_BINDINGS__AGENT: tool_calling
---
apiVersion: v1
kind: Secret
metadata:
  name: graph-agent-service-secret
type: Opaque
stringData:
  LLM__PROFILES__DEFAULT__API_KEY: your-api-key
```

如果你在 Deployment 中修改了 `APP__PORT`，记得同步修改 `containerPort`、Service 和健康探针端口。当前程序不会自动读取 `/config` 挂载目录；如果必须挂载文件，请挂到 `/app/.env` 或 `/app/settings.yaml`。

## CI/CD

项目内置 GitHub Actions 工作流，会在推送到 `main` 或 `master` 时执行测试、构建 Docker 镜像、推送到阿里云 ACR，并更新 GitOps 配置仓库中的部署文件。

需要提前配置以下 GitHub Actions Variables：

- `ACR_DOMAIN`
- `ACR_ZONE`
- `DEPLOY_REPO`
- `DEPLOY_FILE`

需要提前配置以下 GitHub Actions Secrets：

- `ACR_USERNAME`
- `ACR_PASSWORD`
- `GIT_PAT`
