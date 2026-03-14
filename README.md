# OverMindAgent

基于 `FastAPI + LangGraph` 的服务端脚手架，使用 `uv` 管理依赖，配置采用 `Dynaconf` 的 `settings.yaml + .env + 环境变量` 组合。

## 项目结构

```text
.
├── doc/
├── src/overmindagent/api      # FastAPI 路由与依赖
├── src/overmindagent/common   # 配置与公共基础设施
├── src/overmindagent/graphs   # LangGraph builder / registry / state
├── src/overmindagent/llm      # LLM 工厂与 provider 抽象
├── src/overmindagent/nodes    # Graph 节点
├── src/overmindagent/schemas  # Pydantic 输入输出模型
├── src/overmindagent/services # Graph 编排服务
├── tests                      # 测试
├── settings.yaml              # 默认配置
└── .env.example               # 本地覆盖示例
```

## 快速开始

先查看 `settings.yaml`，理解默认配置，再复制本地覆盖模板：

```bash
cp .env.example .env
```

至少补齐以下配置：

```env
LLM__API_KEY=your-api-key
LLM__PROVIDER=openai
LLM__PROTOCOL=responses
LLM__MODEL=gpt-4o-mini
```

安装依赖：

```bash
uv sync
```

按当前配置启动：

```bash
uv run overmindagent
```

开发调试也可以直接使用：

```bash
uv run uvicorn overmindagent.main:app --reload
```

## 配置约定

当前配置加载顺序固定为：

1. `settings.yaml`
2. `.env`
3. 系统环境变量

环境变量使用双下划线映射层级，例如：

```env
APP__PORT=9000
LLM__API_KEY=your-api-key
DATABASE__HOST=127.0.0.1
DATABASE__PASSWORD=""
```

代码中统一按层级访问：

- `settings.app.port`
- `settings.llm.model`
- `settings.database.host`

新增配置时，不需要修改 `config.py`，也不需要维护额外 schema 文件。只要把默认值写进 `settings.yaml`，或直接通过 `.env` / 环境变量覆盖，然后在业务代码里按层级读取即可。

## LangGraph 示例

当前内置一个 `text-analysis` 示例 graph，用于演示：

- `LLM` 配置与模型工厂拆分
- `Graph`、`Node`、`State` 分层
- 基于 `Pydantic` 的结构化输出
- FastAPI 内嵌调用入口

调用示例：

```bash
curl -X POST "http://127.0.0.1:8000/api/graphs/text-analysis/invoke" -H "Content-Type: application/json" -d '{"text":"LangGraph is useful for workflow orchestration.","session_id":"demo-1"}'
```

## 测试

```bash
uv run pytest -q
```

## 文档

- 脚手架说明：`doc/wiki/scaffold.md`
- 新手上手：`doc/tutorial/getting-started.md`

## Docker

构建镜像：

```bash
docker build -t overmindagent:local .
```

通过 `.env` 注入配置运行：

```bash
docker run --rm -p 8000:8000 --env-file .env overmindagent:local
```

镜像内默认只包含 `/app/settings.yaml`。如果要通过文件覆盖配置，请挂载到 `/app/.env` 或 `/app/settings.yaml`；单纯挂载到 `/config` 不会自动生效。

## Kubernetes / k3s

推荐使用 `ConfigMap + Secret` 直接注入环境变量，不需要额外挂载配置目录：

```yaml
envFrom:
  - configMapRef:
      name: over-mind-agent-config
  - secretRef:
      name: over-mind-agent-secret
```

一个最小示例：

```yaml
apiVersion: v1
kind: ConfigMap
metadata:
  name: over-mind-agent-config
data:
  APP__PORT: "8000"
  LLM__PROVIDER: openai
  LLM__PROTOCOL: responses
  LLM__MODEL: gpt-4o-mini
---
apiVersion: v1
kind: Secret
metadata:
  name: over-mind-agent-secret
type: Opaque
stringData:
  LLM__API_KEY: your-api-key
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
