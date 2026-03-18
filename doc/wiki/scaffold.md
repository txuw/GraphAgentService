# OverMindAgent 脚手架说明

本文档整理当前项目的整体结构、运行方式与扩展约定，作为继续扩展 Graph、LLM、Tools 与 API 的统一参考。

## 1. 项目定位

OverMindAgent 是一个基于 `FastAPI + LangGraph` 的服务端项目，重点提供：

- 清晰的 `src/` 分层结构
- 配置驱动的运行方式
- 统一的 Graph 调用入口
- 基于 LangChain `BaseChatModel` 的 LLM 运行时解析
- 接近 LangGraph 官方风格的 `runtime / streaming / schema` 组织方式

当前默认内置两个示例 graph：

- `text-analysis`：演示 `with_structured_output(...)`
- `tool-agent`：演示 `bind_tools(...) + ToolNode + tools_condition`

它们共同体现的核心约定是：

- Graph 相关代码按 feature 收拢到 `graphs/<graph_name>/`
- 跨 graph 可复用工具统一放到 `tools/`
- 运行时模型选择统一通过 `GraphRunContext`
- HTTP / SSE 协议细节停留在 route 与 service 层

## 2. 当前目录结构

```text
.
├── doc/
│   ├── tutorial/
│   │   └── getting-started.md
│   └── wiki/
│       ├── scaffold.md
│       ├── logto-auth.md
│       ├── runtime-binding.md
│       └── sse.md
├── src/overmindagent/
│   ├── api/                    # FastAPI 路由、依赖注入与协议层
│   ├── common/                 # 配置与公共基础设施
│   ├── graphs/                 # Graph 共享基础设施与各 graph 包
│   │   ├── text_analysis/
│   │   └── tool_agent/
│   ├── llm/                    # profile / router / ChatModel factory
│   ├── schemas/                # Pydantic 输入输出模型
│   ├── services/               # Graph 调用与 SSE 编排服务
│   ├── tools/                  # 可复用工具定义与注册
│   ├── __main__.py
│   └── main.py
├── tests/
├── settings.yaml
└── .env.example
```

需要特别注意的边界：

- `src/overmindagent/graphs/` 顶层只放 graph 共享基础设施，如 `registry.py`、`runtime.py`
- 每个 graph 自己的 `builder/state/nodes/prompts` 都放进 `graphs/<graph_name>/`
- 不再使用全局 `nodes/` 目录
- 通用工具通过 `tools/` 暴露，graph 只负责绑定和编排，不承载工具实现细节

## 3. Graph 目录约定

当前推荐每个 graph 使用如下结构：

```text
src/overmindagent/graphs/<graph_name>/
├── __init__.py
├── builder.py
├── state.py
├── nodes.py
└── prompts.py
```

职责约定如下：

- `builder.py`：负责组装 `StateGraph`，并产出 `GraphRuntime`
- `state.py`：定义 `input_schema / state_schema / output_schema`
- `nodes.py`：实现 graph 内部节点逻辑
- `prompts.py`：放该 graph 私有 prompt 模板
- `__init__.py`：暴露 graph 对外入口，通常只导出 `*GraphBuilder`

只有在 graph 复杂度明显上升时，才建议把 `nodes.py` 再拆成 `nodes/` 子目录。默认保持 KISS，先用单文件收拢喵～

## 4. Tools 目录约定

当前 `tool-agent` 已经采用“graph 内聚，tool 外提”的结构：

```text
src/overmindagent/tools/
├── __init__.py
├── registry.py
├── weather.py
├── time.py
└── math.py
```

职责约定如下：

- `weather.py / time.py / math.py`：放可复用工具实现
- `registry.py`：负责组装默认工具集，例如 `build_toolset()`
- `__init__.py`：统一导出常用工具和注册入口

扩展原则：

- 若工具未来可能被多个 graph 复用，直接放入 `tools/`
- 若只是某个 graph 的临时辅助函数，先放该 graph 包内
- 不要把工具实现散落到 route、service 或 schema 层

## 5. LLM 架构

项目不再维护自定义的 `LLMSession / LLMRequest / LLMResponse` 中间协议，而是直接使用 LangChain ChatModel。

核心对象：

- `LLMProfile`
- `LLMRouter`
- `ChatModelFactory`
- `GraphRuntime`
- `GraphRunContext`

可以先用一句话理解这一层：

- `GraphRuntime` 描述“这个 graph 是什么”
- `GraphRunContext` 描述“这次执行里 node 应该怎样拿到模型”

如果需要更深入理解 `Runtime / Binding / Alias / Profile` 的关系，见：

- `doc/wiki/runtime-binding.md`

### `LLMProfile`

文件位置：`src/overmindagent/llm/profile.py`

一个 profile 表示一组真实模型连接配置，例如：

- `provider`
- `api_key`
- `base_url`
- `model`
- `temperature`
- `timeout`
- `max_tokens`
- `provider_options`

### `LLMRouter`

文件位置：`src/overmindagent/llm/router.py`

职责：

- 加载 profiles
- 加载 aliases
- 解析默认 profile
- 将 alias / profile 名称解析到最终 profile
- 通过 `ChatModelFactory` 构造真实 `BaseChatModel`

### `ChatModelFactory`

文件位置：`src/overmindagent/llm/factory.py`

负责 provider 到 ChatModel 构造器的映射。当前内置：

- `openai -> ChatOpenAI`

如果接 OpenAI 兼容服务，通常只需调整 profile 的 `base_url` 和 `model`，不需要改 graph 代码。

### `GraphRunContext`

文件位置：`src/overmindagent/graphs/runtime.py`

这是 graph 运行时上下文，通过 `context_schema` 注入到节点中。当前对节点暴露：

- `resolve_model()`
- `structured_model()`
- `tool_model()`

因此 node 的职责是：

- 声明自己要什么能力
- 构造消息
- 调用模型

而不是：

- 自己读取全局 settings
- 自己决定 provider / model
- 自己处理协议层 metadata

## 6. 配置结构

`src/overmindagent/common/config.py` 只负责三件事：

1. 加载 `settings.yaml`
2. 合并 `.env`
3. 合并系统环境变量

最终优先级固定为：

1. `settings.yaml`
2. `.env`
3. 系统环境变量

LLM 相关配置分为三层：

### `llm.default_profile`

默认 profile 名称。

### `llm.aliases`

能力别名到 profile 的映射，例如：

```yaml
llm:
  aliases:
    general_chat: default
    structured_output: default
    tool_calling: default
```

### `llm.profiles`

真实模型配置，例如：

```yaml
llm:
  profiles:
    default:
      provider: openai
      base_url: null
      model: gpt-4o-mini
      temperature: 0.0
      timeout: 60.0
      max_tokens: null
```

### `graphs.<graph>.llm_bindings`

graph 内部 binding 到 alias / profile 的映射，例如：

```yaml
graphs:
  text-analysis:
    llm_bindings:
      analysis: structured_output
  tool-agent:
    llm_bindings:
      agent: tool_calling
```

环境变量示例：

```env
APP__PORT=9000
LLM__DEFAULT_PROFILE=default
LLM__ALIASES__STRUCTURED_OUTPUT=default
LLM__ALIASES__TOOL_CALLING=default
LLM__PROFILES__DEFAULT__API_KEY=your-api-key
LLM__PROFILES__DEFAULT__BASE_URL=https://your-provider.example/v1
GRAPHS__TEXT_ANALYSIS__LLM_BINDINGS__ANALYSIS=structured_output
GRAPHS__TOOL_AGENT__LLM_BINDINGS__AGENT=tool_calling
DATABASE__HOST=127.0.0.1
```

## 7. Graph 运行时约定

每个 graph builder 都应该产出一个 `GraphRuntime`，至少包含：

- `name`
- `description`
- `graph`
- `input_model`
- `output_model`
- `llm_bindings`
- `stream_modes`

这样 service 和 API 就不需要感知某个 graph 的内部实现。

其中可以把两个对象分开理解：

- `GraphRuntime`：graph 的静态说明书
- `GraphRunContext`：单次执行时注入给 node 的运行上下文

当前把模型调用统一收口到 runtime 层，收口的是“模型解析入口”，不是“全局唯一模型单例”。更深入的说明见：

- `doc/wiki/runtime-binding.md`

### `StateGraph` 约定

推荐使用：

```python
StateGraph(
    state_schema=...,
    context_schema=GraphRunContext,
    input_schema=...,
    output_schema=...,
)
```

设计原则：

- `input_schema` 表示业务输入
- `output_schema` 表示最终业务输出
- `state_schema` 表示 graph 内部状态
- `context_schema` 表示运行时依赖

注意：

- `session_id` 不属于业务输入，而属于运行控制字段
- 当前通过 LangGraph `thread_id` 传递给 graph checkpointer

### 节点职责

推荐：

- 从 `state` 中读取业务状态
- 通过 `runtime.context` 获取模型
- 使用 LangChain message object 构造输入
- 返回结构化状态增量

不推荐：

- 在 node 内部读取全局 settings
- 在 node 内部硬编码 provider / model
- 在 node 内部处理 HTTP / SSE 细节

## 8. `tool-agent` 当前结构

当前 `tool-agent` 位于 `src/overmindagent/graphs/tool_agent/`，采用标准 LangGraph agent 回路：

1. `prepare`
2. `agent`
3. `tools`
4. `finalize`

其中：

- `prepare` 清洗 `query` 并初始化 `messages`
- `agent` 通过 `runtime.context.tool_model(binding="agent", tools=...)` 获取绑定工具后的模型
- `tools` 直接复用 `ToolNode`
- `tools_condition` 决定继续调用工具还是进入 `finalize`
- `finalize` 从消息历史中提取最终答案与工具调用轨迹

状态中的 `messages` 使用：

```python
Annotated[list[AnyMessage], add_messages]
```

这样每一轮模型消息和工具消息都会自动追加到 state 中，最终同时服务于：

- 下一轮 agent 推理
- `answer` 生成
- `tools_used` 生成

默认工具集来自 `overmindagent.tools.build_toolset()`，当前包含：

- `lookup_weather`
- `lookup_local_time`
- `calculate`

扩展工具时的建议顺序：

1. 优先放入 `tools/`，保持可复用
2. 在 `tools/registry.py` 中加入默认装配
3. graph 侧只保留绑定与编排逻辑

## 9. API 与流式输出

系统接口：

- `GET /health`

Graph 接口：

- `GET /api/graphs`
- `POST /api/graphs/{graph_name}/invoke`
- `POST /api/graphs/{graph_name}/stream`

前端聊天流式接口：

- `GET /api/sse/connect`
- `POST /api/chat/execute`

### `GET /api/graphs`

返回 graph 元信息：

- 名称
- 描述
- 输入 schema
- 输出 schema
- 流式模式

### `invoke`

流程：

1. route 接收 payload
2. service 使用 graph 的 `input_model` 做校验
3. service 构造 `GraphRunContext`
4. graph 执行
5. service 使用 `output_model` 校验最终输出

### `stream`

当前流式输出基于：

```python
graph.astream(..., stream_mode=[...], version="v2")
```

当前示例 graph 默认使用：

- `updates`
- `messages`
- `values`

之后由 service 转成 SSE 事件：

- `session`
- `updates`
- `messages`
- `result`
- `completed`
- `error`

更完整的 SSE 协议说明见：

- `doc/wiki/sse.md`

## 10. 新增 Graph 的推荐步骤

基于当前结构，新增一个 graph 的推荐流程是：

1. 在 `schemas/` 中定义输入输出模型
2. 在 `graphs/<graph_name>/state.py` 中定义 graph 的输入、状态与输出 schema
3. 在 `graphs/<graph_name>/nodes.py` 中实现节点逻辑
4. 按需要在 `graphs/<graph_name>/prompts.py` 中抽取 prompt
5. 在 `graphs/<graph_name>/builder.py` 中组装 `StateGraph`
6. 在 `graphs/<graph_name>/__init__.py` 中导出 `*GraphBuilder`
7. 在 `graphs/registry.py` 中注册 runtime
8. 如需工具能力，则从 `tools/` 绑定，不要重建一套全局 `nodes/` 或 graph 私有 `toolset.py`

约定如下：

- Node 负责业务步骤，不负责协议层
- Service 负责统一输入校验、graph 调用和流式输出编排
- Route 只负责 HTTP / SSE 协议映射和异常转换
- LLM 调用统一经过 `GraphRunContext`

## 11. 推荐扩展模板

当项目继续扩展到多 graph、多模型能力和更多工具时，推荐继续沿用下面的形态：

```text
src/overmindagent/
├── api/
│   ├── dependencies.py
│   ├── router.py
│   └── routes/
│       ├── chat.py
│       ├── graphs.py
│       └── system.py
├── common/
│   ├── checkpoint.py
│   ├── config.py
│   └── lifecycle.py
├── graphs/
│   ├── registry.py
│   ├── runtime.py
│   ├── text_analysis/
│   │   ├── __init__.py
│   │   ├── builder.py
│   │   ├── state.py
│   │   ├── nodes.py
│   │   └── prompts.py
│   ├── tool_agent/
│   │   ├── __init__.py
│   │   ├── builder.py
│   │   ├── state.py
│   │   ├── nodes.py
│   │   └── prompts.py
│   └── <future_graph>/
│       ├── __init__.py
│       ├── builder.py
│       ├── state.py
│       ├── nodes.py
│       └── prompts.py
├── llm/
│   ├── factory.py
│   ├── profile.py
│   └── router.py
├── tools/
│   ├── __init__.py
│   ├── registry.py
│   ├── weather.py
│   ├── time.py
│   └── math.py
├── schemas/
│   ├── analysis.py
│   ├── api.py
│   └── tool_agent.py
└── services/
    ├── chat_stream_service.py
    ├── graph_service.py
    └── sse.py
```

演进原则：

- graph 相关代码按 graph 分包，不再恢复全局 `nodes/`
- `graphs/<graph_name>/` 内聚该 graph 的 `builder/state/nodes/prompts`
- 可跨 graph 复用的工具进入 `tools/`
- LLM provider 构造逻辑继续留在 `llm/`
- API 输入输出契约继续集中在 `schemas/`

## 12. 开发与验证

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

如果改动了 graph、工具绑定、模型路由或 provider，建议至少验证：

- `GET /api/graphs`
- 非流式 `invoke`
- 流式 `stream`
- `with_structured_output(...)`
- `bind_tools(...)`
- OpenAI 兼容 `base_url`

## 13. 维护原则

- 保持 API 层轻量
- 保持 Graph 自描述能力稳定
- 优先复用 LangGraph / LangChain 官方能力
- 模型选择放在 runtime context，而不是业务节点里
- 配置层只做加载与合并，不承担业务校验
- 工具实现按复用边界进入 `tools/`，不要回退到 graph 私有 `toolset.py`

## 14. 相关文档

- `doc/wiki/scaffold.md`
- `doc/wiki/logto-auth.md`
- `doc/wiki/runtime-binding.md`
- `doc/wiki/sse.md`
