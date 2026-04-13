# FallBack 机制实现说明

本文档聚焦当前 `GraphAgentService` 中通用 FallBack 机制的实现方式，重点回答这些问题喵：

- 这套机制到底解决什么问题
- 应该按什么顺序读代码，才能最快理解整体实现
- 这次主要改了哪些位置，各自负责什么
- 正常主流程和失败恢复流程是怎么衔接的
- 后续继续扩展时，哪些地方最容易踩坑

如果只记一句话，可以先记这个：

```text
当前 FallBack 机制的核心不是“失败后自动重试”，
而是“把工具失败标准化写回状态，并在下次进入 graph 前先修复脏状态，再继续执行”。
```

## 1. 背景与目标

这套机制主要是为了解决一类典型问题：

```text
LLM 产生 tool_calls
-> tools 执行阶段超时或异常
-> graph 中途退出
-> 同一 session 下次继续执行时复用到不完整 messages
-> LangGraph / ToolNode 因协议不闭合而继续报错
```

当前目标不是实现完整的补偿事务，而是先做好这三件事：

- 给失败增加统一语义，而不是到处直接 `raise Exception`
- 把可恢复的工具失败转成标准 `ToolMessage(status="error")`
- 在同一 `session` 再次进入 graph 前自动修复脏状态

因此这次实现属于：

```text
State Repair + Failure Classification + Structured Tool Error
```

而不是：

```text
LLM fallback provider / 补偿事务 / 自动回滚所有副作用
```

## 2. 建议阅读顺序

如果主人想最快看懂整体实现，建议严格按下面顺序读喵。

### 第一步：先看通用失败模型

先看：

- [failures.py](/D:/code/GraphAgentService/src/graphagentservice/common/failures.py)

这里定义了整套机制最核心的领域抽象：

- `FailureKind`
  - `transient`
  - `protocol`
  - `business`
  - `fatal`
- `RecoveryAction`
  - 写错误消息
  - 修复协议
  - 裁剪到稳定边界
  - 终止 graph
- `RunStatus`
  - `clean`
  - `recoverable_failed`
  - `unrecoverable_failed`
- `GraphRecoveryState`
  - 当前 session 的恢复元信息
- `RecoverableGraphError` / `UnrecoverableGraphError`

这里要先看，是因为后面所有逻辑都围绕这几个概念在转。

### 第二步：再看失败分类器

再看：

- [failure_classifier.py](/D:/code/GraphAgentService/src/graphagentservice/services/failure_classifier.py)

它负责把原始异常翻译成统一决策：

```text
原始异常
-> FailureClassifier.classify(...)
-> FailureDecision(kind/action/recoverable/message)
```

阅读时重点看：

- 为什么 `transient` 判断优先于 `protocol`
- 什么情况下会认为是协议错误
- 什么情况下会直接判成 `fatal`

理解这个文件后，主人就能知道“为什么这次超时会补错误消息，而不是直接裁剪状态”。

### 第三步：看状态修复服务

然后看：

- [state_repair.py](/D:/code/GraphAgentService/src/graphagentservice/services/state_repair.py)

这是整套机制里最关键的恢复逻辑，负责处理：

- 孤儿 `ToolMessage`
- 重复 `ToolMessage`
- 已声明但缺失结果的 `tool_call_id`
- `last_stable_message_count` 的回退与重算

这里建议重点看这几个函数：

- `StateRepairService.repair()`
- `build_error_tool_message()`
- `_find_duplicate_tool_message_indexes()`
- `_find_orphan_tool_message_indexes()`

主人读这个文件时，可以把它理解成：

```text
给一串可能已经脏掉的 messages，
尽量修成一串还能继续喂给 LangGraph 的合法 messages。
```

### 第四步：看 ToolNode 是如何接入 fallback 的

再看：

- [tool_execution.py](/D:/code/GraphAgentService/src/graphagentservice/services/tool_execution.py)

重点看 `ObservedToolNode.ainvoke()`。

这里是“失败第一次发生时”的处理入口：

```text
tools 真正执行
-> 捕获异常
-> 分类
-> 更新 recovery 状态
-> 可恢复则写回 ToolMessage(status="error")
-> 不可恢复则把状态标成 unrecoverable_failed
```

这一步非常关键，因为它决定了失败是：

- 当场变成结构化错误消息继续流转
- 还是留待下次入口修复
- 还是直接终止当前 session

### 第五步：看 GraphService 的前置恢复

然后看：

- [graph_service.py](/D:/code/GraphAgentService/src/graphagentservice/services/graph_service.py)

重点看：

- `_repair_state_if_needed()`
- `invoke()`
- `stream_events()`
- `resume_stream_events()`

这是“失败之后下一次再进 graph 时”的入口处理。

可以把它理解成：

```text
请求进入 graph 前
-> 先读 checkpoint state
-> 看 recovery.run_status
-> 如果状态脏了，先 repair
-> repair 完再真正执行 graph
```

如果不看这里，主人会只看到“异常被转成 ToolMessage”，但看不懂为什么下次同一 session 能恢复。

### 第六步：最后看 graph 自身怎么维护 recovery

最后再看 graph 节点：

- [tool_agent/state.py](/D:/code/GraphAgentService/src/graphagentservice/graphs/tool_agent/state.py)
- [tool_agent/nodes.py](/D:/code/GraphAgentService/src/graphagentservice/graphs/tool_agent/nodes.py)
- [plan_analyze/state.py](/D:/code/GraphAgentService/src/graphagentservice/graphs/plan_analyze/state.py)
- [plan_analyze/nodes.py](/D:/code/GraphAgentService/src/graphagentservice/graphs/plan_analyze/nodes.py)

这里重点关注两件事：

- graph state 新增了 `recovery`
- 什么时候更新 `last_stable_message_count`

这部分本质上是“graph 对通用恢复机制的接线层”，不是规则核心。

## 3. 本次主要代码变更点

### 3.1 新增通用失败模型

位置：

- [failures.py](/D:/code/GraphAgentService/src/graphagentservice/common/failures.py)

职责：

- 定义统一失败类型
- 定义恢复动作
- 定义 session 的恢复状态结构
- 给上层提供标准异常模型

为什么放在 `common`

- 因为这里是纯抽象，不依赖 graph、service、API
- 后续新增 graph 时可以直接复用，不必重新定义一套异常体系

### 3.2 新增失败分类器

位置：

- [failure_classifier.py](/D:/code/GraphAgentService/src/graphagentservice/services/failure_classifier.py)

职责：

- 把第三方异常、超时、协议错误翻译成统一 `FailureDecision`

为什么放在 `services`

- 它是运行时行为，不是纯常量
- 它依赖 messages 和执行上下文，不适合塞进 common

### 3.3 新增状态修复服务

位置：

- [state_repair.py](/D:/code/GraphAgentService/src/graphagentservice/services/state_repair.py)

职责：

- 修复脏消息
- 维护稳定边界
- 必要时补 `ToolMessage(status="error")`

### 3.4 改造 ToolNode

位置：

- [tool_execution.py](/D:/code/GraphAgentService/src/graphagentservice/services/tool_execution.py)

职责变化：

- 以前只做观测
- 现在同时负责工具失败标准化

这里是本次改造最重要的行为变化点之一。

### 3.5 GraphService 增加前置恢复

位置：

- [graph_service.py](/D:/code/GraphAgentService/src/graphagentservice/services/graph_service.py)

职责变化：

- 以前只是直接执行 graph
- 现在执行前先检查 checkpoint state 是否为脏状态

### 3.6 两个 message-driven graph 接入 recovery

位置：

- [tool_agent/state.py](/D:/code/GraphAgentService/src/graphagentservice/graphs/tool_agent/state.py)
- [tool_agent/nodes.py](/D:/code/GraphAgentService/src/graphagentservice/graphs/tool_agent/nodes.py)
- [plan_analyze/state.py](/D:/code/GraphAgentService/src/graphagentservice/graphs/plan_analyze/state.py)
- [plan_analyze/nodes.py](/D:/code/GraphAgentService/src/graphagentservice/graphs/plan_analyze/nodes.py)

职责变化：

- state 增加 `recovery`
- 成功闭合一轮时更新稳定边界

## 4. 运行链路怎么理解

### 4.1 正常主流程

正常情况下链路如下：

```text
请求进入 GraphService
-> 检查 recovery，发现是 clean
-> graph 正常执行
-> agent 产出 tool_calls
-> tools 正常返回 ToolMessage
-> graph 更新 last_stable_message_count
-> 继续下一轮或结束
```

这条链路不会触发修复扫描。

也就是说，正常路径新增的开销很小，主要只是多维护一个 `recovery` 字段。

### 4.2 工具失败的第一现场

如果失败发生在 tools 执行阶段：

```text
ObservedToolNode 捕获异常
-> FailureClassifier 分类
-> 若为 recoverable + write_tool_error_message
-> 生成 ToolMessage(status="error")
-> 更新 recovery.run_status = recoverable_failed
-> graph 继续把错误消息写入状态
```

这一步的价值是：

- 不再只有日志里知道失败
- agent 可以在下一步看到错误消息并自主决定怎么继续

### 4.3 同一 session 再次进入

下一次请求进入时：

```text
GraphService._repair_state_if_needed()
-> 读取 checkpoint state
-> 检查 recovery.run_status
-> 若为 recoverable_failed
-> 调 StateRepairService 修复 messages
-> 用 aupdate_state 写回
-> 再真正执行 graph
```

这里就是“脏 session 隔离与修复”的核心。

### 4.4 不可恢复状态

如果状态已经无法修复：

```text
run_status = unrecoverable_failed
-> GraphService 拒绝继续执行当前 session
```

当前实现会直接阻止继续跑这条脏线程，而不是带着坏状态硬跑。

## 5. 重点关注的几个实现细节

### 5.1 `last_stable_message_count`

这是理解恢复逻辑最重要的字段之一。

它代表：

```text
messages 到哪个位置为止，一定是协议闭合、可以安全继续复用的。
```

当前实现里，只有在这些时机才更新它：

- `prepare` 写入初始用户消息后
- `tools` 成功返回一轮结果后
- `agent` 直接产出最终答案且没有 `tool_calls`

不要在 `agent` 刚产出 `tool_calls` 时更新它。
因为这时工具结果还没回来，协议还没闭合。

### 5.2 为什么 `transient` 判断优先于 `protocol`

如果工具执行时发生超时，消息表面上看当然也是“未闭合”的。

但如果先按 `protocol` 处理，就会把这类本质上的临时失败误判成状态损坏，导致：

- 不写错误 `ToolMessage`
- agent 失去可见错误输入
- 恢复动作跑偏

所以当前实现里：

```text
超时 / 网络抖动 / 429
优先判 transient
而不是因为“tool result 缺失”直接判 protocol
```

这是很关键的判定顺序。

### 5.3 为什么 `ObservedToolNode` 要无论 stream/invoke 都统一使用

如果只有 SSE 流式路径走增强版 ToolNode，而同步 `invoke` 仍走原生 `ToolNode`，就会出现：

- stream 场景能 fallback
- invoke 场景不能 fallback

这会导致同一 graph 在不同入口行为不一致。

所以现在 `tool-agent` 和 `plan-analyze` 都统一走 `ObservedToolNode`，即使没有 emitter 也一样。

### 5.4 为什么 `recovery` 收敛成一个对象

当前没有把这些字段平铺进 state：

- `run_status`
- `last_failure`
- `pending_actions`
- `recovery_attempts`
- `last_stable_message_count`

而是统一收敛到：

```text
state["recovery"]
```

这样做的好处是：

- graph state 侵入性小
- 后续加字段只改一个嵌套对象
- 非 message-driven graph 可以选择完全不接入

## 6. 常见坑

### 坑 1：把 ToolMessage 的 `status="error"` 当脏数据删掉

不能这样做。

当前设计里，合法的错误 `ToolMessage` 是恢复机制的一部分，不是脏数据。

应删除的是：

- 孤儿 `ToolMessage`
- 重复 `ToolMessage`

不应删除的是：

- 有合法 `tool_call_id` 且明确表示工具失败的 `ToolMessage(status="error")`

### 坑 2：稳定边界更新过早

如果在 `AIMessage.tool_calls` 刚写入时就把它当稳定点，会导致：

- 出错后裁剪不到真正安全的位置
- 下次恢复仍然带着半轮 tool 调用历史

### 坑 3：只改 ToolNode，不改 GraphService 入口

只改第一现场还不够。

因为真正解决“下次继续炸”的关键在于：

```text
下次进入 graph 前，是否先修复脏状态
```

所以 `GraphService._repair_state_if_needed()` 是必须有的。

### 坑 4：把所有未知异常都当 recoverable

不能这么做。

像配置错误、代码 bug、状态结构损坏这类问题，如果还继续跑，只会把状态污染扩大。

当前默认策略是：

```text
能明确识别为 transient/business/protocol 的，才走恢复
其余默认 fatal
```

### 坑 5：未来新增 message-driven graph 时只复制节点逻辑，不接 recovery

后续如果新增新的 `messages + tools_condition + ToolNode` graph，至少要同步接入：

- state 增加 `recovery`
- graph 中成功闭合时更新 `last_stable_message_count`
- `_build_tool_node()` 统一返回 `ObservedToolNode`

否则新 graph 会重新掉回旧问题。

## 7. 调试建议

如果后续主人要排查某个 session 的异常，建议按下面顺序查：

1. 看当前 checkpoint state 里的 `recovery`
2. 看 `messages` 里最后一轮 `AIMessage.tool_calls` 是否闭合
3. 看有没有孤儿或重复 `ToolMessage`
4. 看 `ObservedToolNode` 记录的失败是 `transient/protocol/business/fatal` 哪一类
5. 看 `GraphService` 是否在下一次请求前执行了 `_repair_state_if_needed()`

如果只想快速判断是不是“脏 session”问题，可以先看这个：

```text
recovery.run_status != clean
```

## 8. 当前边界与后续演进

当前这套机制已经解决了：

- 工具失败后的标准化状态写回
- 同一 session 再次进入时的自动修复
- 基础的协议清洗与脏状态隔离

但还没有解决：

- 有副作用工具的补偿事务
- 更细粒度的运维修复接口
- 更完整的错误码对外暴露

如果后续继续演进，建议优先顺序是：

1. 给关键工具补 `ToolBusinessError` 和更明确的业务失败语义
2. 增加 session recovery 诊断接口
3. 只有在出现真实副作用场景后，再补 `compensate()` 协议

## 9. 相关代码位置

当前 FallBack 机制的关键代码主要在这些位置：

- [failures.py](/D:/code/GraphAgentService/src/graphagentservice/common/failures.py)
- [failure_classifier.py](/D:/code/GraphAgentService/src/graphagentservice/services/failure_classifier.py)
- [state_repair.py](/D:/code/GraphAgentService/src/graphagentservice/services/state_repair.py)
- [tool_execution.py](/D:/code/GraphAgentService/src/graphagentservice/services/tool_execution.py)
- [graph_service.py](/D:/code/GraphAgentService/src/graphagentservice/services/graph_service.py)
- [tool_agent/state.py](/D:/code/GraphAgentService/src/graphagentservice/graphs/tool_agent/state.py)
- [tool_agent/nodes.py](/D:/code/GraphAgentService/src/graphagentservice/graphs/tool_agent/nodes.py)
- [plan_analyze/state.py](/D:/code/GraphAgentService/src/graphagentservice/graphs/plan_analyze/state.py)
- [plan_analyze/nodes.py](/D:/code/GraphAgentService/src/graphagentservice/graphs/plan_analyze/nodes.py)
- [test_fallback_recovery.py](/D:/code/GraphAgentService/test/test_fallback_recovery.py)

## 10. 结论

当前 FallBack 机制的实现重点，不在于“多加几层 try/except”，而在于把失败变成一种可建模、可落盘、可修复的状态喵。

如果用一句更短的话总结：

```text
先把失败写进状态，再在下次执行前修状态，
而不是让脏状态直接带着历史继续跑。
```
