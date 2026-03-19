# Runtime 与 Binding 机制

本文档聚焦解释 GraphAgentService 中 `Runtime / Binding / Alias / Profile` 之间的关系，以及为什么项目要把模型调用入口统一收口到 runtime 层。

## 1. 一句话概括

可以把当前设计理解为：

```text
node 只声明“我要什么能力”
-> graph 提供本地 binding 映射
-> runtime 统一解析 alias / profile
-> router / factory 构造真实 BaseChatModel
```

这里统一收口的是“模型解析入口”，不是“全局唯一模型单例”喵～

## 2. 核心对象

### `GraphRuntime`

`GraphRuntime` 是 graph 的静态说明书，用来描述：

- graph 叫什么
- graph 的输入输出模型是什么
- graph 的 `llm_bindings` 是什么
- graph 支持哪些 `stream_modes`

它回答的是：“这个 graph 是什么”。

### `GraphRunContext`

`GraphRunContext` 是一次 graph 执行时注入给 node 的运行上下文，主要职责是：

- 解析 binding
- 解析 alias / profile
- 统一给模型补充 tags 和 metadata
- 暴露 `resolve_model()`、`structured_model()`、`tool_model()`

它回答的是：“这次执行里，node 应该怎样拿到模型”。

### `LLMRouter`

`LLMRouter` 负责：

- 加载 `profiles`
- 加载 `aliases`
- 解析默认 profile
- 将 alias / profile 名称解析到最终 profile
- 调用 `ChatModelFactory` 构造真实模型

### `ChatModelFactory`

`ChatModelFactory` 负责 provider 到具体 `BaseChatModel` 的构造。

它回答的是：“已知最终 profile，应该怎样创建真实模型实例”。

## 3. 名词表

### `profile`

`profile` 是真实模型配置名，例如 `default`。  
它对应的是 provider、model、base_url、timeout 这些硬配置。

### `alias`

`alias` 是平台级能力别名，例如：

- `structured_output`
- `tool_calling`

它的作用是把“能力”与“具体 profile”解耦。

### `binding`

`binding` 是 graph 内部节点使用的局部能力名，例如：

- `analysis`
- `agent`
- `planner`
- `executor`

它的作用是把“graph 内部角色”与“全局能力别名”解耦。

## 4. 两段映射链路

当前 binding 机制本质上是两段映射：

```text
binding
-> graph.llm_bindings
-> alias / profile
-> llm.aliases
-> profile
-> BaseChatModel
```

也可以更具体地写成：

```text
node 中的 binding
-> 当前 graph 的 llm_bindings
-> 全局 alias
-> 真实 profile
-> ChatModelFactory 创建模型
```

## 5. 当前项目中的配置示例

当前配置位于 `settings.yaml`：

```yaml
llm:
  aliases:
    structured_output: default
    tool_calling: default

graphs:
  text-analysis:
    llm_bindings:
      analysis: structured_output
  tool-agent:
    llm_bindings:
      agent: tool_calling
```

这表示：

- `text-analysis` graph 内部的 `analysis`，最终会走到 `structured_output -> default`
- `tool-agent` graph 内部的 `agent`，最终会走到 `tool_calling -> default`

## 6. 一次调用是怎么解析的

以 `text-analysis` 为例，node 中的调用方式是：

```python
runtime.context.structured_model(binding="analysis", schema=...)
```

它的实际解析过程是：

```text
analysis
-> graphs.text-analysis.llm_bindings.analysis
-> structured_output
-> llm.aliases.structured_output
-> default
-> llm.profiles.default
-> ChatOpenAI(...)
```

`tool-agent` 也是同样的思路，只是 node 调用的是：

```python
runtime.context.tool_model(binding="agent", tools=...)
```

## 7. 为什么不让 node 直接写 profile

如果 node 直接写 `default`、`gpt-4o-mini`、`openai`，会有几个问题：

- graph 代码直接依赖具体模型实现
- 一旦切换 provider / model，需要到多个 graph 里逐个修改
- 各 graph 很容易出现超时、tags、metadata、重试策略不一致
- 很难知道“哪个 graph 的哪个角色”正在使用哪个模型

binding 机制就是为了把这些变化点收敛到统一入口。

## 8. Runtime 收口的真实优势

### 1. 一处调整，多处生效

这是最直观的收益。比如：

- 切换 alias 指向
- 替换 profile
- 修改 timeout / base_url / provider
- 给所有模型统一增加 tags / metadata

都可以在统一入口调整，而不必改每个 graph 的 node。

### 2. graph 与具体模型实现解耦

node 只表达“我要结构化输出能力”或“我要工具调用能力”，而不是直接绑定某个 provider。

这能让 graph 更稳定，也更容易迁移到不同模型。

### 3. 统一观测与排障

runtime 在模型创建前会统一补充：

- `graph:<graph_name>`
- `binding:<binding>`
- `profile:<profile>`

这样后续做日志、Tracing、成本分析时，更容易回答：

- 哪个 graph 在调用模型
- graph 中哪个角色在调用
- 最终落到了哪个 profile

### 4. 保持平台级策略一致

后续如果要增加这些能力：

- 统一重试
- fallback
- 限流
- 缓存
- 审计
- token / cost 统计

优先都应该放在 runtime / router 一层，而不是分散到各个 node。

### 5. 测试更简单

graph 的 node 不直接 new model，测试时更容易：

- 替换 `LLMRouter`
- 注入假的 `GraphRunContext`
- 验证 binding 是否正确解析

### 6. 既统一，又保留 graph 自由度

统一入口并不代表所有 graph 必须共用同一个模型角色名。

例如：

- `text-analysis` 可以用 `analysis`
- `tool-agent` 可以用 `agent`
- 未来复杂 graph 可以用 `planner / executor / critic`

每个 graph 仍然可以保留自己的业务语义，只是最终解析过程被统一收口。

## 9. 常见误区

### 误区 1：这是不是模型全局单例

不是。

当前设计里共享的是 `LLMRouter` 这类解析与构造入口，而不是“全局唯一模型实例”。

`GraphRunContext` 是按每次 graph 调用创建的单次运行上下文。  
所以更准确的说法是：

- 模型解析入口统一
- 执行上下文按调用生成

### 误区 2：binding 就是 profile

不是。

`binding` 是 graph 内部角色名，`profile` 是真实模型配置名，中间通常还隔着一层 `alias`。

推荐保持三层职责分明：

- graph 用 `binding`
- 平台用 `alias`
- 配置层用 `profile`

### 误区 3：runtime 应该接管所有业务逻辑

也不是。

runtime 负责的是：

- 模型解析
- 模型包装
- 观测信息补充

runtime 不应该负责：

- 业务编排
- 状态流转
- 协议层转换
- graph 专属业务判断

## 10. 推荐使用方式

推荐这样使用 binding 机制：

1. node 中始终写语义化 binding，例如 `analysis`、`agent`
2. graph builder 为当前 graph 提供默认 `llm_bindings`
3. 全局通过 `llm.aliases` 管理共享能力别名
4. `profile` 只在配置层出现，不泄漏到 node

## 11. 一个更复杂的扩展示例

如果未来新增一个多角色 graph，可以这样设计：

```yaml
llm:
  aliases:
    planning: default
    execution: default
    review: default

graphs:
  multi-role-agent:
    llm_bindings:
      planner: planning
      executor: execution
      critic: review
```

那么 graph 内部 node 可以分别写：

```python
runtime.context.resolve_model(binding="planner")
runtime.context.tool_model(binding="executor", tools=...)
runtime.context.structured_model(binding="critic", schema=...)
```

这样 graph 内部语义清楚，平台层切换模型也很方便。

## 12. 结论

binding 机制的核心价值，不只是“一处改动，多处生效”，更重要的是：

- 把模型选择从业务 graph 中剥离出去
- 把平台级策略收口到统一入口
- 让 graph 保持业务语义，避免直接依赖 provider / model 细节

如果用一句更短的话总结：

`Runtime 负责把“我要什么能力”翻译成“实际该调用哪个模型，以及带什么运行时包装”。`
