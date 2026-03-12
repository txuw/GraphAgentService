# OverMindAgent

使用 `uv` 管理依赖与运行环境的 FastAPI + LangGraph 项目，采用 `src/` 目录结构、`.env` 配置加载，以及适合继续扩展 Agent/Graph 的脚手架分层。

## 项目结构

```text
.
├── src/overmindagent/api      # FastAPI 路由与依赖
├── src/overmindagent/common   # 配置与共享基础设施
├── src/overmindagent/graphs   # LangGraph builder / registry / state
├── src/overmindagent/llm      # LLM 工厂与 provider 抽象
├── src/overmindagent/nodes    # Graph 节点
├── src/overmindagent/schemas  # Pydantic 输入输出模型
├── src/overmindagent/services # Graph 编排服务
├── tests                # 测试
└── deploy/k3s           # k3s 部署清单
```

## 快速开始

创建本地配置：

```bash
cp .env.example .env
```

安装依赖：

```bash
uv sync
```

启动开发服务：

```bash
uv run uvicorn overmindagent.main:app --reload
```

按 `.env` 中的配置启动：

```bash
uv run overmindagent
```

## LangGraph 示例

当前内置一个 `text-analysis` 示例 graph，用于演示：

- `LLM` 配置与模型工厂拆分
- `Graph`、`Node`、`State` 分层
- 基于 `Pydantic` 的结构化输出
- FastAPI 内嵌调用入口

调用示例：

```bash
curl -X POST "http://127.0.0.1:8000/api/graphs/text-analysis/invoke" \
  -H "Content-Type: application/json" \
  -d '{"text":"LangGraph is useful for workflow orchestration.","session_id":"demo-1"}'
```

## 测试

```bash
uv run pytest
```

## 文档

- 脚手架说明：`doc/wiki/scaffold.md`
- 新手上手：`doc/tutorial/getting-started.md`

## Docker

构建镜像：

```bash
docker build -t overmindagent:local .
```

通过环境文件运行：

```bash
docker run --rm -p 8000:8000 --env-file .env overmindagent:local
```

## k3s 部署

1. 准备 k3s 环境变量文件：

```bash
cp deploy/k3s/.env.example deploy/k3s/.env
```

2. 按实际镜像地址修改 `deploy/k3s/kustomization.yaml` 中的 `images` 配置。
3. 部署到 k3s：

```bash
kubectl apply -k deploy/k3s
```

部署清单会通过 `ConfigMap` 注入环境变量，并使用 `/health` 作为容器探针。当前 k3s 清单默认要求 `OVERMIND_PORT=8000`，这样可以和 Service 与探针保持一致。

## CI/CD

项目内置了一条 GitHub Actions 工作流，会在推送到 `main` 或 `master` 时执行测试、构建 Docker 镜像、推送到阿里云 ACR，并更新 GitOps 配置仓库中的部署文件。

需要提前配置以下 GitHub Actions Variables：

- `ACR_DOMAIN`
- `ACR_ZONE`
- `DEPLOY_REPO`
- `DEPLOY_FILE`

需要提前配置以下 GitHub Actions Secrets：

- `ACR_USERNAME`
- `ACR_PASSWORD`
- `GIT_PAT`
