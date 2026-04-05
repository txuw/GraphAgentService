[根目录](../../CLAUDE.md) > [src](../) > **graphs**

# Graphs 模块

> 最后更新：2026-04-04

## 模块职责

Graphs 模块是 GraphAgentService 的核心编排层，负责：

- **Graph 注册与发现**：统一管理所有 Graph 的元信息与运行时实例
- **运行时上下文**：为 Graph 节点提供 LLM 模型、认证信息、请求上下文
- **Checkpoint 管理**：支持 Graph 状态持久化（内存或 PostgreSQL）
- **Graph 模板**：提供常见 Graph 模式的参考实现

## 目录结构

```
graphs/
├── __init__.py              # 模块导出
├── registry.py              # Graph 注册表
├── runtime.py               # 运行时上下文与元信息
├── text_analysis/           # 文本分析 Graph
├── tool_agent/              # 工具调用 Agent Graph
├── plan_analyze/            # 计划分析 Graph
├── image_agent/             # 图像 Agent Graph
└── image_analyze_calories/  # 卡路里分析 Graph
```

## 核心组件

### GraphRegistry (`registry.py`)

**职责**：
- 管理所有 Graph 的 `GraphRuntime` 实例
- 提供 Graph 发现与查询接口
- 应用配置覆盖（MCP 服务器、LLM 绑定）

**关键方法**：
```python
def get(graph_name: str) -> GraphRuntime
def list_names() -> tuple[str, ...]
def list_runtimes() -> tuple[GraphRuntime, ...]
```

### GraphRuntime (`runtime.py`)

**职责**：
- 描述 Graph 的静态元信息
- 提供 Graph 的可执行对象
- 定义输入/输出 schema

**核心字段**：
```python
@dataclass(frozen=True)
class GraphRuntime:
    name: str                              # Graph 唯一标识
    description: str                       # Graph 描述
    graph: Any                             # LangGraph CompiledGraph
    input_model: type[BaseModel]           # 输入 Pydantic 模型
    output_model: type[BaseModel]          # 输出 Pydantic 模型
    llm_bindings: Mapping[str, str]        # LLM 绑定配置
    mcp_servers: tuple[str, ...]           # MCP 服务器列表
    stream_modes: tuple[str, ...]          # 流式模式
```

### GraphRunContext (`runtime.py`)

**职责**：
- 单次 Graph 执行时的运行时上下文
- 通过 `context_schema` 注入到节点中
- 提供统一的模型获取接口

**核心方法**：
```python
def resolve_model(
    *,
    binding: str | None = None,
    profile: str | None = None,
) -> BaseChatModel

def structured_model(
    *,
    schema: type[BaseModel],
    binding: str | None = None,
) -> BaseChatModel

def tool_model(
    *,
    tools: Sequence[Any],
    binding: str | None = None,
) -> BaseChatModel
```

## 子模块说明

### text_analysis

**功能**：文本分析 Graph，演示结构化输出模式

**特点**：
- 使用 `with_structured_output()` 生成固定格式分析
- 包含预处理、分析、空处理、最终化节点
- 不涉及工具调用

**使用场景**：情感分析、文本分类、内容提取

### tool_agent

**功能**：工具调用 Agent，演示标准 LangGraph 工具循环

**特点**：
- 使用 `bind_tools()` + `ToolNode` + `tools_condition`
- 支持多轮工具调用
- 自动合并本地工具与 MCP 工具

**使用场景**：需要调用外部 API 的任务（天气查询、时间查询等）

### plan_analyze

**功能**：计划分析 Graph，演示 MCP 工具集成

**特点**：
- 最简工具回路：`START -> analyze <-> tools -> END`
- 运行时动态解析 MCP 工具
- 适合调试 MCP 链路

**使用场景**：需要集成远端 MCP 服务的任务

### image_agent

**功能**：图像 Agent，支持多模态输入

**特点**：
- 支持图像 URL + 文本混合输入
- 使用多模态 LLM（如 Gemini）
- 结构化输出

**使用场景**：视觉问答、图像理解

### image_analyze_calories

**功能**：卡路里分析 Graph

**特点**：
- 专门针对食物图像分析
- 结构化输出营养成分

**使用场景**：健康饮食分析

## Graph 开发指南

### 标准目录结构

```
your_graph/
├── __init__.py      # 导出 GraphBuilder
├── state.py         # 定义 input/state/output schema
├── nodes.py         # 实现节点逻辑
├── prompts.py       # Prompt 模板（可选）
└── builder.py       # 组装 StateGraph
```

### State 定义模式

```python
from typing_extensions import TypedDict, Annotated
from langgraph.graph import add_messages

class MyGraphInput(TypedDict):
    query: str

class MyGraphState(TypedDict, total=False):
    query: str
    messages: Annotated[list[AnyMessage], add_messages]
    result: str | None

class MyGraphOutput(TypedDict):
    answer: str
```

### Builder 模式

```python
from langgraph.graph import StateGraph, START, END

class MyGraphBuilder:
    name = "my-graph"
    description = "My custom graph"

    def __init__(self, graph_settings, checkpointer):
        self._graph_settings = graph_settings or {}
        self._nodes = MyGraphNodes()
        self._checkpointer = checkpointer

    def build(self) -> GraphRuntime:
        graph = StateGraph(
            state_schema=MyGraphState,
            context_schema=GraphRunContext,
            input_schema=MyGraphInput,
            output_schema=MyGraphOutput,
        )

        # 添加节点
        graph.add_node("step1", self._nodes.step1)
        graph.add_node("step2", self._nodes.step2)

        # 添加边
        graph.add_edge(START, "step1")
        graph.add_edge("step1", "step2")
        graph.add_edge("step2", END)

        # 编译
        compile_kwargs = {}
        if self._checkpointer:
            compile_kwargs["checkpointer"] = self._checkpointer

        return GraphRuntime(
            name=self.name,
            description=self.description,
            graph=graph.compile(**compile_kwargs),
            input_model=MyGraphRequest,
            output_model=MyGraphOutput,
            llm_bindings=self._llm_bindings(),
            stream_modes=("updates", "messages", "values"),
        )

    def _llm_bindings(self) -> dict[str, str]:
        configured = self._graph_settings.get("llm_bindings", {})
        if hasattr(configured, "items"):
            return {str(k): str(v) for k, v in configured.items()}
        return {"default": "structured_output"}
```

### Node 模式

```python
class MyGraphNodes:
    async def step1(
        self,
        state: MyGraphState,
        runtime: GraphRunContext,
    ) -> dict[str, object]:
        # 获取模型
        model = runtime.context.structured_model(
            binding="analysis",
            schema=MyOutputSchema,
        )

        # 构造消息
        messages = [HumanMessage(content=state["query"])]

        # 调用模型
        result = await model.ainvoke(messages)

        # 返回状态更新
        return {"result": result}
```

## 注册 Graph

在 `registry.py` 的 `create_graph_registry` 函数中添加：

```python
def create_graph_registry(settings, checkpoint_provider) -> GraphRegistry:
    checkpointer = checkpoint_provider.build()
    graph_overrides = _graph_overrides(settings)
    runtimes = (
        # ... 现有 Graph ...
        MyGraphBuilder(
            graph_settings=graph_overrides.get(MyGraphBuilder.name, {}),
            checkpointer=checkpointer,
        ).build(),
    )
    # ...
```

## 配置示例

在 `settings.yaml` 中配置 Graph：

```yaml
graphs:
  my-graph:
    llm_bindings:
      analysis: structured_output
      agent: tool_calling
    mcp_servers: ["sport"]  # 可选
```

## 常见问题

### Q: Graph 状态如何持久化？

A: 通过 `checkpointer` 参数控制：
- 内存模式：`GRAPH__CHECKPOINT_MODE=memory`
- PostgreSQL 模式：配置 `GRAPH__CHECKPOINT__POSTGRES__URL`

### Q: 如何支持 MCP 工具？

A:
1. 在 `settings.yaml` 配置 `mcp.connections`
2. 在 Graph 配置中添加 `mcp_servers`
3. 在 Node 中调用 `runtime.context.mcp_tool_resolver.resolve_tools()`
4. 使用 `runtime.context.tool_model()` 绑定工具

### Q: 如何调试 Graph？

A:
1. 使用 `plan-analyze` 作为最简模板
2. 开启 `GRAPH__DEBUG=true` 查看详细日志
3. 检查 `state` 是否包含 `messages`（工具循环必需）
4. 验证 LLM binding 是否正确

## 相关文件清单

- `__init__.py` - 模块导出
- `registry.py` - Graph 注册表
- `runtime.py` - 运行时上下文
- `text_analysis/` - 文本分析 Graph
- `tool_agent/` - 工具调用 Agent Graph
- `plan_analyze/` - 计划分析 Graph
- `image_agent/` - 图像 Agent Graph
- `image_analyze_calories/` - 卡路里分析 Graph

## 变更记录

### 2026-04-04
- 初始化 Graphs 模块文档
- 补充开发指南与配置示例
