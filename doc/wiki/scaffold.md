# OverMindAgent 脚手架说明

本文档整理当前项目的整体结构、运行方式与扩展约定，作为继续扩展 Graph、LLM 和 API 的统一参考。

## 1. 项目定位

OverMindAgent 是一个基于 `FastAPI + LangGraph` 的服务端项目，重点提供：

- 清晰的 `src/` 分层结构
- 配置驱动的运行方式
- 统一的 Graph 调用入口
- 基于 LangChain `BaseChatModel` 的 LLM 运行时解析
- 贴近 LangGraph 官方实践的 runtime / streaming / schema 组织方式

当前默认内置两个示例 graph，用来演示：

- `text-analysis`：`with_structured_output(...)` 结构化输出
- `tool-agent`：`bind_tools(...) + ToolNode + tools_condition`

它们共同演示：

- Graph / Node / State / Runtime 的职责划分
- `context_schema` 注入运行时依赖
- LangGraph `v2` 流式事件对外暴露

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
├─ src/overmindagent/graphs   # Graph builder / registry / runtime / state
├─ src/overmindagent/llm      # profile / router / ChatModel factory
├─ src/overmindagent/nodes    # Graph 节点
├─ src/overmindagent/schemas  # Pydantic 输入输出模型
├─ src/overmindagent/services # Graph 服务编排
├─ tests                      # 测试
├─ settings.yaml              # 默认配置
└─ .env.example               # 本地覆盖示例
```

## 3. LLM 架构

当前项目不再维护自定义 `LLMSession`、`LLMRequest`、`LLMResponse` 这类中间协议，而是直接使用 LangChain ChatModel。

核心对象：

- `LLMProfile`
- `LLMRouter`
- `ChatModelFactory`
- `GraphRunContext`

职责拆分如下：

### `LLMProfile`

`src/overmindagent/llm/profile.py`

一个 profile 代表一个真实模型连接配置，例如：

- `provider`
- `api_key`
- `base_url`
- `model`
- `temperature`
- `timeout`
- `max_tokens`
- `provider_options`

### `LLMRouter`

`src/overmindagent/llm/router.py`

负责：

- 加载 profiles
- 加载 aliases
- 解析默认 profile
- 按名称找到最终 profile
- 通过 `ChatModelFactory` 产出真实 `BaseChatModel`

### `ChatModelFactory`

`src/overmindagent/llm/factory.py`

负责 provider 到 ChatModel 构造器的映射。

当前内置：

- `openai -> ChatOpenAI`

如果接 OpenAI 兼容服务，通常只需要改 profile 的 `base_url` 和 `model`，不需要改 graph 代码。

### `GraphRunContext`

`src/overmindagent/graphs/runtime.py`

这是 graph 运行时上下文，作为 `context_schema` 注入给节点使用。它向节点暴露：

- `resolve_model()`
- `structured_model()`
- `tool_model()`

因此 node 的职责变成：

- 声明自己要什么能力
- 构造消息
- 调用模型

而不是：

- 自己读取全局配置
- 自己决定 provider / model
- 自己处理 observability tag / metadata

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

当前与 LLM 相关的关键配置分成三层：

### `llm.default_profile`

默认 profile 名称。

### `llm.aliases`

能力别名到 profile 的映射，例如：

```yaml
llm:
  aliases:
    general_chat: default
    multimodal: default
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

代码中统一按层级属性访问配置：

- `settings.app.port`
- `settings.llm.profiles.default.model`
- `settings.graphs.text_analysis.llm_bindings.analysis`
- `settings.database.host`

## 5. Graph 架构

当前每个 graph builder 都应该产出一个 `GraphRuntime`：

- `name`
- `description`
- `graph`
- `input_model`
- `output_model`
- `llm_bindings`
- `stream_modes`

这样 service 和 API 就不用知道某个 graph 的细节，只需要消费统一 runtime。

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

- `input_schema` 表示 graph 业务输入
- `output_schema` 表示 graph 最终输出
- `state_schema` 表示 graph 内部状态
- `context_schema` 表示运行期依赖

注意：

- `session_id` 不属于业务输入，而属于运行控制字段
- 当前通过 LangGraph `thread_id` 传给 graph checkpointer

### 节点职责

当前 node 推荐：

- 从 state 中读取原始业务数据
- 用 `runtime.context` 获取模型
- 用 LangChain message object 构造输入
- 返回结构化的状态更新

不推荐：

- 在 node 内读取全局 settings
- 把 provider/model 名称写死在 node
- 在 node 内维护 HTTP / SSE 细节

## 6. API 与流式输出

系统接口：

- `GET /health`

Graph 接口：

- `GET /api/graphs`
- `POST /api/graphs/{graph_name}/invoke`
- `POST /api/graphs/{graph_name}/stream`

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
2. service 根据 graph 的 `input_model` 做校验
3. service 构造 `GraphRunContext`
4. graph 执行
5. service 用 `output_model` 校验最终输出

### `stream`

当前流式输出基于：

```python
graph.astream(..., stream_mode=[...], version="v2")
```

当前 `text-analysis` 的 `stream_modes` 为：

- `updates`
- `messages`
- `values`

然后由 service 转成 SSE：

- `session`
- `updates`
- `messages`
- `result`
- `completed`
- `error`

含义：

- `updates`：graph 节点状态更新
- `messages`：底层 ChatModel 的流式 message chunk
- `result`：最终 graph 输出

## 7. Graph 扩展规范

新增一个 Graph 的建议步骤：

1. 在 `schemas/` 中定义输入输出模型
2. 在 `graphs/` 中定义 `state_schema`
3. 在 `nodes/` 中实现节点逻辑
4. 在 `graphs/` 中新增 `*GraphBuilder`
5. 产出 `GraphRuntime`
6. 在 `graphs/registry.py` 中注册 graph runtime
7. 确认是否需要自定义 `stream_modes`

约定如下：

- Node 负责业务步骤，不直接处理 HTTP 细节
- Service 负责统一输入校验、graph 调用和流式输出编排
- Route 只负责协议层转换和异常映射
- LLM 调用统一经过 `GraphRunContext`

## 8. 模型与 Provider 扩展规范

### 新增一个模型配置

如果只是换模型、换 endpoint 或增加 OpenAI 兼容服务：

1. 新增或修改 `llm.profiles`
2. 视情况增加 `llm.aliases`
3. 在具体 graph 的 `llm_bindings` 中切换绑定

这类改动通常不需要碰 node 代码。

### 新增一个 provider

如果要接一个新的 provider：

1. 在 `src/overmindagent/llm/factory.py` 中新增 builder
2. 让 builder 返回对应的 `BaseChatModel`
3. 补测试

不要重新引入项目级的自定义 session 协议，优先保持在 LangChain ChatModel 生态内扩展。

## 9. LangGraph 风格建议

为了保持与官方思路一致，建议遵守：

- state 存业务原始数据和中间结果，不存全局模型对象
- runtime context 存运行期依赖，不存业务数据
- node 单一职责，一个 node 只完成一个明确步骤
- 路由、结构化输出、工具调用在 node 内显式声明
- 流式输出优先复用 LangGraph / LangChain 原生事件

进一步扩展时：

- 结构化输出：`runtime.context.structured_model(...)`
- 工具调用：`runtime.context.tool_model(...)`
- 多模态输入：在 node 里直接构造 LangChain message content blocks

## 10. 多 Graph / 多 Model / 多模态 / Tool Graph 推荐目录模板

当项目继续扩展到多个 graph、多个模型能力、多模态输入和工具型 agent graph 时，推荐逐步从当前的简化结构演进到按 graph 领域分包的目录。

推荐模板如下：

```text
src/overmindagent/
├─ api/
│  ├─ routes/
│  │  ├─ graphs.py
│  │  └─ system.py
│  └─ dependencies.py
├─ common/
│  ├─ checkpoint.py
│  └─ config.py
├─ graphs/
│  ├─ registry.py
│  ├─ runtime.py
│  ├─ text_analysis/
│  │  ├─ __init__.py
│  │  ├─ builder.py
│  │  ├─ state.py
│  │  ├─ nodes.py
│  │  └─ prompts.py
│  ├─ tool_agent/
│  │  ├─ __init__.py
│  │  ├─ builder.py
│  │  ├─ state.py
│  │  ├─ nodes.py
│  │  ├─ prompts.py
│  │  └─ toolset.py
│  └─ multimodal_chat/
│     ├─ __init__.py
│     ├─ builder.py
│     ├─ state.py
│     ├─ nodes.py
│     └─ prompts.py
├─ llm/
│  ├─ profile.py
│  ├─ router.py
│  ├─ factory.py
│  └─ providers/
│     ├─ __init__.py
│     └─ openai_chat.py
├─ tools/
│  ├─ __init__.py
│  ├─ weather.py
│  ├─ search.py
│  └─ retrieval.py
├─ schemas/
│  ├─ api.py
│  ├─ text_analysis.py
│  ├─ tool_agent.py
│  └─ multimodal_chat.py
└─ services/
   └─ graph_service.py
```

推荐按下面的原则演进：

- graph 相关代码按 graph 分包，而不是继续把所有 node/state/prompt 平铺到全局目录。
- `graphs/<graph_name>/` 内聚该 graph 的 builder、state、nodes、prompts、graph 私有工具。
- 跨 graph 复用的工具放到 `tools/`，不要把所有工具都塞进某个 graph 包里。
- LLM provider 构造逻辑继续放在 `llm/`，保持 graph 不直接依赖某个 SDK。
- API 输入输出 schema 可以按业务域拆文件，但仍放在 `schemas/` 作为对外契约层。

针对不同类型 graph，建议这样放：

### 多 Graph

- 一个 graph 一个目录。
- graph 自己维护 `builder.py`、`state.py`、`nodes.py`。
- 如果 graph 很复杂，再把 `nodes.py` 拆成 `nodes/` 子目录。

### 多 Model

- 模型配置继续集中在 `llm.profiles` 和 `llm.aliases`。
- graph 内只声明 binding，例如 `planner`、`executor`、`vision`、`tool_calling`。
- 如果某个 graph 有多模型协作，把 binding 写在该 graph 的 `llm_bindings` 里，不要把 profile 名直接写进 node。

### 多模态

- 建议单独建 `graphs/multimodal_*` 包。
- 多模态消息构造放在该 graph 的 `nodes.py` 或 `prompts.py`。
- 图像、音频、文件等输入预处理可以单独拆 `attachments.py` 或 `inputs.py`，避免普通文本 graph 被多模态细节污染。

### Tool Graph / Agent Graph

- 如果 graph 的主目标是工具调用，建议单独建 `graphs/tool_agent/` 或更明确的业务名目录。
- graph 私有工具放 `toolset.py`。
- 通用工具实现放 `tools/`，graph 内只负责绑定和编排。
- agent graph 的 state 往往更复杂，建议把计划、工具调用记录、中间观察结果单独放在该 graph 的 state 模型里。

如果继续扩展，推荐优先按照“业务 graph 分包”演进，而不是继续扩大全局 `nodes/` 目录。前者更符合 LangGraph 以 workflow/graph 为中心组织代码的思路，也更接近多数开源项目在规模增长后的维护方式。

## 11. 开发与验证

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

如果你改了 graph、模型路由或 provider，建议至少验证：

- `GET /api/graphs`
- 非流式 `invoke`
- 流式 `stream`
- `with_structured_output(...)`
- `bind_tools(...)`
- OpenAI 兼容 `base_url`

## 12. 维护原则

- 保持 API 层轻量
- 保持 Graph 自描述能力稳定
- 优先复用 LangGraph / LangChain 官方能力
- 模型选择放在 runtime context，而不是业务节点里
- 配置层只做加载与合并，不承担业务校验
- 注释只用于解释协议映射、流式事件转换和非显然逻辑
