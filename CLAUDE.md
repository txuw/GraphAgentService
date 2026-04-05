# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 项目概述

GraphAgentService 是基于 FastAPI + LangGraph 的 AI 工作流编排服务，使用 uv 管理依赖，Dynaconf 管理配置（`settings.yaml` → `.env` → 环境变量三级叠加）。

## 常用命令

```bash
# 安装依赖
uv sync

# 启动服务
uv run graphagentservice

# 开发模式（热重载）
uv run uvicorn graphagentservice.main:app --reload

# 测试
uv run pytest -q
```

## 架构

### 请求生命周期

```
HTTP Request → API Route → GraphService → GraphRegistry → CompiledGraph(Node)
                                        ↗ LLMRouter → BaseChatModel
                                        ↗ MCPToolResolver → 远端工具
```

### 核心模块

| 模块 | 路径 | 职责 |
|------|------|------|
| API | `api/` | FastAPI 路由、依赖注入、SSE 流式端点 |
| Graphs | `graphs/` | Graph 注册表、运行时上下文、5 个内置 Graph 实现 |
| LLM | `llm/` | Profile/别名/Binding 三层 LLM 路由，ChatModel 工厂 |
| MCP | `mcp/` | 远端工具服务集成（streamable_http）、工具合并与缓存 |
| Tools | `tools/` | 本地可复用工具（math/time/weather）与注册入口 |
| Services | `services/` | Graph 执行编排、SSE 事件分发、Plan 摘要 |
| Common | `common/` | 配置加载、认证（Logto）、checkpoint、中间件、日志 |

### LLM 三层解析链路

```
binding ("analysis")                          # Graph 节点声明
→ graph config (text-analysis.analysis)       # Graph 级绑定
→ alias (structured_output)                   # 能力别名
→ profile (default)                          # 真实模型配置
→ BaseChatModel (ChatOpenAI)                 # 可执行实例
```

Node 中统一通过 `runtime.context.structured_model(binding=..., schema=...)` 或 `runtime.context.tool_model(binding=..., tools=...)` 获取模型，不直接依赖 provider 或模型名。

### Graph 开发约定

每个 Graph 遵循固定结构：

```
graph_name/
├── __init__.py   # 导出 GraphBuilder
├── state.py      # Input/State/Output TypedDict
├── nodes.py      # 节点实现（接收 state + runtime: GraphRunContext）
├── prompts.py    # Prompt 模板（可选）
└── builder.py    # 组装 StateGraph → GraphRuntime
```

新增 Graph 后在 `graphs/registry.py` 的 `create_graph_registry` 中注册。

### 流式输出

使用 LangGraph v2 streaming，通过 SSE 返回事件：`session` → `updates` → `messages` → `result` → `completed`/`error`。

### MCP 工具集成

- Graph 配置 `mcp_servers` 指定远端服务器
- Node 运行时调用 `runtime.context.mcp_tool_resolver.resolve_tools()` 动态解析
- 请求头（含 Authorization）自动透传到 MCP 服务器
- ToolNode 必须在运行时创建，不能在 builder 阶段固化

## 配置约定

- 环境变量用双下划线映射层级：`LLM__PROFILES__DEFAULT__API_KEY`
- 配置优先级：系统环境变量 > `.env` > `settings.yaml`
- Checkpoint 模式：`GRAPH__CHECKPOINT_MODE=memory`（默认）或配置 PostgreSQL URL

## 子模块文档

- [Graphs 模块](src/graphagentservice/graphs/CLAUDE.md) — Graph 开发指南、注册流程、调试建议
- [LLM 模块](src/graphagentservice/llm/CLAUDE.md) — Profile 系统、别名路由、Provider 扩展
- [MCP 模块](src/graphagentservice/mcp/CLAUDE.md) — 工具解析策略、请求透传、调试指南

## 代码规范

- Python >= 3.11，使用类型注解
- 代码注释语言与项目保持一致（中文）
- 异步优先：Node 和 Service 方法使用 `async def`
