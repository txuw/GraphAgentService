# OverMindAgent 新手上手

这是一份给第一次接触这个项目的开发者准备的快速教程。目标很简单：先把服务跑起来，再理解当前的 Graph 和 LLM 配置链路。

## 1. 你会得到什么

跑完这份教程后，你应该能做到：

- 启动项目
- 配置一个可用的 LLM
- 理解 `settings.yaml + .env + 环境变量` 的配置方式
- 调用 `text-analysis` 图
- 知道以后该从哪里扩展代码

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
LLM__MODEL=gpt-4o-mini
DATABASE__HOST=127.0.0.1
```

代码里统一这样读取：

- `settings.app.port`
- `settings.llm.model`
- `settings.database.host`

如果后续你要新增配置，不需要修改 `config.py`。只要在 `settings.yaml` 增加默认值，或直接在 `.env` / 环境变量里写入对应键，然后在代码里按层级访问即可。

## 4. 配置 LLM

至少要补这几个配置：

```env
LLM__API_KEY=your-api-key
LLM__PROVIDER=openai
LLM__PROTOCOL=responses
LLM__MODEL=gpt-4o-mini
```

如果你接的是 OpenAI 兼容服务，还可以配置：

```env
LLM__BASE_URL=https://your-provider.example/v1
```

关键点：

- `LLM__PROVIDER` 决定使用哪个 provider adapter
- `LLM__PROTOCOL` 决定走 `responses` 还是 `chat`
- 业务代码不会因为这两个配置变化而改动

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

## 6. 调用第一个 Graph

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

流式接口会返回 SSE 事件，例如：

- `session`
- `text_delta`
- `tool_call`
- `tool_result`
- `completed`

如果底层 provider 没有触发 tool 调用，你只会看到文本和完成事件，这很正常。

## 7. 部署时的配置建议

如果你部署到 Docker、Kubernetes 或 k3s，优先使用环境变量注入配置。

- Docker 可以直接用 `--env-file .env`
- Kubernetes / k3s 推荐 `ConfigMap + Secret` 配合 `env` 或 `envFrom`
- 当前程序不会自动读取 `/config` 挂载目录

如果你一定要用文件挂载，请挂到 `/app/.env` 或 `/app/settings.yaml`。如果只挂到 `/config`，配置不会自动生效。

## 8. 看懂代码应该从哪里开始

如果你是第一次读这个项目，建议按这个顺序看：

1. `src/overmindagent/main.py`
2. `src/overmindagent/api/routes/graphs.py`
3. `src/overmindagent/services/graph_service.py`
4. `src/overmindagent/graphs/registry.py`
5. `src/overmindagent/nodes/text_analysis.py`
6. `src/overmindagent/llm/`

理解顺序是：

- API 接到请求
- Service 调用 graph
- Graph 调用 node
- Node 构造 `LLMRequest`
- `LLMSession` 根据 `settings.llm.provider` / `settings.llm.protocol` 选择具体 adapter

## 9. 以后常见的两种扩展

新增一个 Graph：

1. 定义新的 schema
2. 定义新的 state
3. 写 node
4. 写 graph builder
5. 注册到 graph registry

新增一个 Provider：

1. 在 `src/overmindagent/llm/` 新增 adapter
2. 实现统一的 `invoke()` / `stream()`
3. 在 `LLMSessionFactory` 注册
4. 补 provider 对应测试

## 10. 什么时候看更完整的设计文档

当你需要理解这些内容时，再去看 Wiki 文档：

- 为什么要统一 `LLMSession`
- Provider / Protocol 如何切换
- Graph、Service、Route 的职责边界
- 配置约定和部署约定

对应文档在：

- `doc/wiki/scaffold.md`

## 11. 验证改动

做完代码修改后，至少跑一次：

```bash
uv run pytest -q
```

如果你改了 LLM 适配层，建议额外验证：

- `responses` 协议
- `chat` 协议
- 非流式
- 流式
- 结构化输出
