"""plan_analyze Graph 的节点实现。

拓扑：START → memory_recall → analyze ↔ tools → memory_commit → END

节点说明：
  - memory_recall：读链 — 从 Mem0 检索相关记忆，注入分析上下文
  - analyze：核心分析节点 — 注入记忆 + MCP 工具 + ask_user_questions
  - tools：工具执行节点 — 包含 MCP 工具和提问工具
  - memory_commit：写链 — 将本轮对话关键信息异步入队写入 Mem0
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from typing import Any

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langchain_core.tools import BaseTool
from langgraph.prebuilt import ToolNode, tools_condition
from langgraph.runtime import Runtime

from graphagentservice.graphs.runtime import GraphRunContext

from .prompts import ANALYSIS_PROMPT_TEMPLATE, SYSTEM_ANALYSIS_PROMPT
from .state import PlanAnalyzeGraphState
from .tools import ask_user_questions

_logger = logging.getLogger(__name__)


class PlanAnalyzeNodes:
    def __init__(
        self,
        analysis_binding: str = "analysis",
    ) -> None:
        self._analysis_binding = analysis_binding

    # ------------------------------------------------------------------
    # 读链：记忆检索
    # ------------------------------------------------------------------

    async def memory_recall(
        self,
        state: PlanAnalyzeGraphState,
        runtime: Runtime[GraphRunContext],
    ) -> dict[str, str]:
        """从 Mem0 检索与 query 相关的记忆，注入到分析上下文。"""
        memory = runtime.context.memory_provider
        user_id = self._resolve_user_id(runtime)

        if memory is None or not user_id:
            return {"user_id": user_id, "related_memories": ""}

        query = state.get("query", "")
        try:
            results = memory.search(query, user_id=user_id)
        except Exception:
            _logger.warning("Memory recall failed", exc_info=True)
            return {"user_id": user_id, "related_memories": ""}

        filtered = [
            m for m in results.get("results", [])[:5]
            if m.get("score", 0) > 0.3
        ]
        if filtered:
            texts = [f"- {m['memory']}" for m in filtered]
            return {
                "user_id": user_id,
                "related_memories": "[用户相关记忆]\n" + "\n".join(texts),
            }
        return {"user_id": user_id, "related_memories": ""}

    # ------------------------------------------------------------------
    # 核心分析节点
    # ------------------------------------------------------------------

    async def analyze(
        self,
        state: PlanAnalyzeGraphState,
        runtime: Runtime[GraphRunContext],
    ) -> dict:
        query = state.get("query", "").strip()
        if not query:
            return {
                "query": "",
                "plan": "",
                "analysis": "No query provided.",
                "messages": [],
            }

        # 构建消息（含记忆上下文）
        messages = self.build_analysis_messages(
            query=query,
            plan=state.get("plan", ""),
            messages=state.get("messages", ()),
            related_memories=state.get("related_memories", ""),
        )

        # 解析 MCP 工具 + 始终注入提问工具
        tools = await self._resolve_tools(runtime)
        tools.append(ask_user_questions)

        if tools:
            model = runtime.context.tool_model(
                binding=self._analysis_binding,
                tools=tools,
                tags=("analysis", "tool-calling"),
            )
        else:
            model = runtime.context.resolve_model(
                binding=self._analysis_binding,
                tags=("analysis",),
            )

        response = await model.ainvoke(messages)
        result: dict = {
            "query": query,
            "plan": state.get("plan", ""),
            "messages": [response],
        }
        if not response.tool_calls:
            result["analysis"] = self._content_to_text(response)
        return result

    # ------------------------------------------------------------------
    # 工具执行节点
    # ------------------------------------------------------------------

    async def tools(
        self,
        state: PlanAnalyzeGraphState,
        runtime: Runtime[GraphRunContext],
    ) -> dict[str, list[object]]:
        resolved_tools = await self._resolve_tools(runtime)
        # 注入提问工具到工具节点
        resolved_tools.append(ask_user_questions)
        tool_node = _build_tool_node(resolved_tools, runtime)
        result = await tool_node.ainvoke(state, runtime=runtime)
        if isinstance(result, dict):
            return result
        return {"messages": list(result)}

    # ------------------------------------------------------------------
    # 写链：记忆提交（异步入队，不阻塞主流程）
    # ------------------------------------------------------------------

    async def memory_commit(
        self,
        state: PlanAnalyzeGraphState,
        runtime: Runtime[GraphRunContext],
    ) -> dict:
        """将本轮关键信息异步入队写入 Mem0。"""
        commit_worker = runtime.context.memory_commit_worker
        if commit_worker is None:
            return {}

        user_id = state.get("user_id", "")
        if not user_id:
            return {}

        messages = state.get("messages", [])
        commit_messages = self._extract_commit_messages(messages)
        if not commit_messages:
            return {}

        key = f"{runtime.context.trace_id}:{len(messages)}"
        await commit_worker.enqueue(user_id, commit_messages, key)
        return {}

    # ------------------------------------------------------------------
    # 路由
    # ------------------------------------------------------------------

    def route_after_analyze(self, state: PlanAnalyzeGraphState) -> str:
        if not state.get("messages"):
            return "__end__"
        return tools_condition(state)

    # ------------------------------------------------------------------
    # 消息构建
    # ------------------------------------------------------------------

    @staticmethod
    def build_analysis_messages(
        *,
        query: str,
        plan: str,
        messages: Sequence[object] = (),
        related_memories: str = "",
    ) -> list[object]:
        # 构建系统提示（含记忆注入）
        system_content = SYSTEM_ANALYSIS_PROMPT
        if related_memories:
            system_content = f"{system_content}\n\n{related_memories}"

        human_content = ANALYSIS_PROMPT_TEMPLATE.format(query=query, plan=plan)

        if messages:
            return [
                SystemMessage(content=system_content),
                HumanMessage(content=human_content),
                *list(messages),
            ]
        return [
            SystemMessage(content=system_content),
            HumanMessage(content=human_content),
        ]

    # ------------------------------------------------------------------
    # 内部工具方法
    # ------------------------------------------------------------------

    async def _resolve_tools(
        self,
        runtime: Runtime[GraphRunContext],
    ) -> list[BaseTool]:
        if not runtime.context.mcp_servers:
            return []

        resolver = runtime.context.mcp_tool_resolver
        if resolver is None:
            return []

        return await resolver.resolve_tools(
            graph_name=runtime.context.graph_name,
            server_names=runtime.context.mcp_servers,
            current_user=runtime.context.current_user,
            request_headers=dict(runtime.context.request_headers),
        )

    @staticmethod
    def _resolve_user_id(runtime: Runtime[GraphRunContext]) -> str:
        """从 AuthenticatedUser 中提取用户标识。"""
        user = runtime.context.current_user
        return getattr(user, "user_id", "") or getattr(user, "sub", "") or ""

    @staticmethod
    def _extract_commit_messages(
        messages: Sequence[object],
    ) -> list[dict[str, str]]:
        """从消息列表中提取适合写入 Mem0 的用户/AI 消息对。"""
        commit_msgs: list[dict[str, str]] = []
        for msg in messages:
            role = ""
            content = ""
            if isinstance(msg, HumanMessage):
                role = "user"
                content = msg.content if isinstance(msg.content, str) else str(msg.content)
            elif isinstance(msg, AIMessage):
                # 跳过纯工具调用的 AI 消息
                if msg.tool_calls:
                    continue
                role = "assistant"
                content = msg.content if isinstance(msg.content, str) else str(msg.content)

            if role and content:
                commit_msgs.append({"role": role, "content": content})
        return commit_msgs

    @staticmethod
    def _content_to_text(response: Any) -> str:
        content = response.content if isinstance(response, AIMessage) else response
        if isinstance(content, str):
            return content.strip()
        if isinstance(content, list):
            parts: list[str] = []
            for block in content:
                if isinstance(block, dict) and "text" in block:
                    parts.append(str(block["text"]))
                else:
                    parts.append(str(block))
            return "\n".join(parts).strip()
        return str(content).strip()


def _build_tool_node(
    tools: list[BaseTool],
    runtime: Runtime[GraphRunContext],
) -> ToolNode:
    from graphagentservice.services.tool_execution import ObservedToolNode

    emitter = runtime.context.tool_stream_emitter
    if emitter is not None:
        return ObservedToolNode(tools, emitter=emitter)
    return ToolNode(tools)
