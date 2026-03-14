# OverMindAgent 脚手架说明

本文档整理当前项目的整体结构、运行方式与扩展约定，作为继续扩展 Graph、LLM Provider 和 API 的统一参考。

## 1. 项目定位

OverMindAgent 是一个基于 `FastAPI + LangGraph` 的服务端项目，重点提供：

- 清晰的 `src/` 分层结构
- 配置驱动的运行方式
- 统一的 Graph 调用入口
- 可切换 Provider / Protocol 的 LLM 会话层

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
├─ tests                      # 测试
├─ settings.yaml              # 默认配置
└─ .env.example               # 本地覆盖示例
```

## 3. LLM 架构

当前 LLM 层不直接向业务暴露某个 SDK 的原始对象，而是统一收敛到会话接口：

- `LLMSession`
- `LLMRequest`
- `LLMResponse`
- `LLMEvent`

`LLMSessionFactory` 根据配置动态创建 session，核心读取项为：

- `settings.llm.provider`
- `settings.llm.protocol`
- `settings.llm.model`
- `settings.llm.base_url`

当前已实现：

- `openai + responses`
- `openai + chat`

后续新增 Provider 时，原则上只需要：

1. 新增一个 adapter
2. 在 factory 中注册
3. 补测试

业务层不应该直接依赖某个 SDK 的原始对象。

## 4. 配置架构

当前项目刻意保持 Dynaconf 的原生、弱约束用法，不做额外 schema 校验，也不维护 `settings.schema.json`。

`src/overmindagent/common/config.py` 只负责三件事：

1. 加载 `settings.yaml`
2. 合并 `.env`
3. 合并系统环境变量

最终优先级固定为：

1. `settings.yaml`
2. `.env`
3. 系统环境变量

环境变量使用双下划线映射层级，例如：

```env
APP__PORT=9000
LLM__API_KEY=your-api-key
GRAPH__CHECKPOINT_MODE=memory
DATABASE__HOST=127.0.0.1
```

代码中统一通过层级属性访问配置：

- `settings.app.port`
- `settings.llm.model`
- `settings.graph.default_name`
- `settings.database.host`

新增配置时，不需要修改 `config.py`。只要配置源里出现这个键，Dynaconf 就会暴露出来。例如：

```yaml
database:
  driver: postgresql
  host: localhost
  port: 5432
```

```env
DATABASE__HOST=127.0.0.1
DATABASE__PASSWORD=my_super_secret_password
```

对应代码直接读取：

```python
settings.database.host
settings.database.password
```

## 5. 部署约定

Docker 镜像内默认只包含 `/app/settings.yaml`。

推荐的部署方式：

- 本地开发：`settings.yaml + .env`
- Docker：`--env-file .env`
- Kubernetes / k3s：`ConfigMap + Secret` 注入环境变量

当前程序不会自动读取 `/config` 挂载目录。如果你沿用如下挂载：

```yaml
volumeMounts:
  - name: config
    mountPath: /config
```

那么配置不会自动生效。要么直接用环境变量注入，要么把文件挂到 `/app/.env` 或 `/app/settings.yaml`。

如果你修改了 `APP__PORT`，还需要同步调整：

- `containerPort`
- Service 端口
- `livenessProbe` / `readinessProbe`

## 6. API 入口

系统接口：

- `GET /health`

Graph 接口：

- `POST /api/graphs/{graph_name}/invoke`
- `POST /api/graphs/{graph_name}/stream`

调用示例：

```bash
curl -X POST "http://127.0.0.1:8000/api/graphs/text-analysis/invoke" -H "Content-Type: application/json" -d '{"text":"LangGraph is useful for workflow orchestration.","session_id":"demo-1"}'
```

流式调用示例：

```bash
curl -N -X POST "http://127.0.0.1:8000/api/graphs/text-analysis/stream" -H "Content-Type: application/json" -d '{"text":"LangGraph is useful for workflow orchestration.","session_id":"demo-1"}'
```

## 7. Graph 扩展规范

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

## 8. 开发与验证

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

## 9. 维护原则

- 保持 API 层轻量
- 保持 LLM 统一出口稳定
- 优先复用 SDK 已有能力，不重复造轮子
- 新增 Provider 时不修改业务节点逻辑
- 配置层只做加载与合并，不承担业务校验
- 注释只用于解释协议映射、流式事件转换和非显然逻辑
