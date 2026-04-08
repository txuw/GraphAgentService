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
import time
from collections.abc import Sequence
from typing import Any

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langchain_core.tools import BaseTool
from langgraph.prebuilt import ToolNode, tools_condition
from langgraph.runtime import Runtime

from graphagentservice.graphs.runtime import GraphRunContext
from graphagentservice.common.logging import context_extra

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
        started = time.perf_counter()
        _logger.info(
            "Memory recall node started",
            extra=context_extra(
                event="graph_node_started",
                graph=runtime.context.graph_name,
                sessionId=runtime.context.session_id,
                requestId=runtime.context.request_id,
                pageId=runtime.context.page_id,
                node="memory_recall",
                status="started",
            ),
        )
        memory = runtime.context.memory_provider
        user_id = self._resolve_user_id(runtime)

        if memory is None or not user_id:
            _logger.info(
                "Memory recall skipped",
                extra=context_extra(
                    event="memory_recall_skipped",
                    graph=runtime.context.graph_name,
                    sessionId=runtime.context.session_id,
                    requestId=runtime.context.request_id,
                    pageId=runtime.context.page_id,
                    userId=user_id or "-",
                    node="memory_recall",
                    status="skipped",
                ),
            )
            result = {"user_id": user_id, "related_memories": ""}
            _logger.info(
                "Memory recall node completed",
                extra=context_extra(
                    event="graph_node_completed",
                    graph=runtime.context.graph_name,
                    sessionId=runtime.context.session_id,
                    requestId=runtime.context.request_id,
                    pageId=runtime.context.page_id,
                    userId=user_id or "-",
                    node="memory_recall",
                    status="completed",
                    elapsedMs=round((time.perf_counter() - started) * 1000),
                ),
            )
            return result

        query = state.get("query", "")
        try:
            results = memory.search(query, user_id=user_id)
        except Exception as exc:
            _logger.exception(
                "Memory recall node failed",
                extra=context_extra(
                    event="graph_node_failed",
                    graph=runtime.context.graph_name,
                    sessionId=runtime.context.session_id,
                    requestId=runtime.context.request_id,
                    pageId=runtime.context.page_id,
                    userId=user_id,
                    node="memory_recall",
                    status="failed",
                    elapsedMs=round((time.perf_counter() - started) * 1000),
                    errorType=type(exc).__name__,
                ),
            )
            return {"user_id": user_id, "related_memories": ""}

        filtered = [
            m for m in results.get("results", [])[:5]
            if m.get("score", 0) > 0.3
        ]
        if filtered:
            _logger.info(
                "Memory recall completed",
                extra=context_extra(
                    event="memory_recall_completed",
                    graph=runtime.context.graph_name,
                    sessionId=runtime.context.session_id,
                    requestId=runtime.context.request_id,
                    pageId=runtime.context.page_id,
                    userId=user_id,
                    node="memory_recall",
                    status="completed",
                    hitCount=len(filtered),
                ),
            )
            texts = [f"- {m['memory']}" for m in filtered]
            result = {
                "user_id": user_id,
                "related_memories": "[用户相关记忆]\n" + "\n".join(texts),
            }
            _logger.info(
                "Memory recall node completed",
                extra=context_extra(
                    event="graph_node_completed",
                    graph=runtime.context.graph_name,
                    sessionId=runtime.context.session_id,
                    requestId=runtime.context.request_id,
                    pageId=runtime.context.page_id,
                    userId=user_id,
                    node="memory_recall",
                    status="completed",
                    elapsedMs=round((time.perf_counter() - started) * 1000),
                ),
            )
            return result
        result = {"user_id": user_id, "related_memories": ""}
        _logger.info(
            "Memory recall node completed",
            extra=context_extra(
                event="graph_node_completed",
                graph=runtime.context.graph_name,
                sessionId=runtime.context.session_id,
                requestId=runtime.context.request_id,
                pageId=runtime.context.page_id,
                userId=user_id,
                node="memory_recall",
                status="completed",
                elapsedMs=round((time.perf_counter() - started) * 1000),
            ),
        )
        return result

    # ------------------------------------------------------------------
    # 核心分析节点
    # ------------------------------------------------------------------

    async def analyze(
        self,
        state: PlanAnalyzeGraphState,
        runtime: Runtime[GraphRunContext],
    ) -> dict:
        started = time.perf_counter()
        _logger.info(
            "Plan analyze node started",
            extra=context_extra(
                event="graph_node_started",
                graph=runtime.context.graph_name,
                sessionId=runtime.context.session_id,
                requestId=runtime.context.request_id,
                pageId=runtime.context.page_id,
                node="analyze",
                status="started",
            ),
        )
        query = state.get("query", "").strip()
        if not query:
            result = {
                "query": "",
                "plan": "",
                "analysis": "No query provided.",
                "messages": [],
            }
            _logger.info(
                "Plan analyze node completed",
                extra=context_extra(
                    event="graph_node_completed",
                    graph=runtime.context.graph_name,
                    sessionId=runtime.context.session_id,
                    requestId=runtime.context.request_id,
                    pageId=runtime.context.page_id,
                    node="analyze",
                    status="completed",
                    elapsedMs=round((time.perf_counter() - started) * 1000),
                ),
            )
            return result

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

        try:
            response = await model.ainvoke(messages)
            result: dict = {
                "query": query,
                "plan": state.get("plan", ""),
                "messages": [response],
            }
            if not response.tool_calls:
                result["analysis"] = self._content_to_text(response)
        except Exception as exc:
            _logger.exception(
                "Plan analyze node failed",
                extra=context_extra(
                    event="graph_node_failed",
                    graph=runtime.context.graph_name,
                    sessionId=runtime.context.session_id,
                    requestId=runtime.context.request_id,
                    pageId=runtime.context.page_id,
                    node="analyze",
                    status="failed",
                    elapsedMs=round((time.perf_counter() - started) * 1000),
                    errorType=type(exc).__name__,
                ),
            )
            raise
        _logger.info(
            "Plan analyze node completed",
            extra=context_extra(
                event="graph_node_completed",
                graph=runtime.context.graph_name,
                sessionId=runtime.context.session_id,
                requestId=runtime.context.request_id,
                pageId=runtime.context.page_id,
                node="analyze",
                status="completed",
                elapsedMs=round((time.perf_counter() - started) * 1000),
            ),
        )
        return result

    # ------------------------------------------------------------------
    # 工具执行节点
    # ------------------------------------------------------------------

    async def tools(
        self,
        state: PlanAnalyzeGraphState,
        runtime: Runtime[GraphRunContext],
    ) -> dict[str, list[object]]:
        started = time.perf_counter()
        _logger.info(
            "Plan analyze tools node started",
            extra=context_extra(
                event="graph_node_started",
                graph=runtime.context.graph_name,
                sessionId=runtime.context.session_id,
                requestId=runtime.context.request_id,
                pageId=runtime.context.page_id,
                node="tools",
                status="started",
            ),
        )
        try:
            resolved_tools = await self._resolve_tools(runtime)
            resolved_tools.append(ask_user_questions)
            tool_node = _build_tool_node(resolved_tools, runtime)
            result = await tool_node.ainvoke(state, runtime=runtime)
            payload = result if isinstance(result, dict) else {"messages": list(result)}
        except Exception as exc:
            _logger.exception(
                "Plan analyze tools node failed",
                extra=context_extra(
                    event="graph_node_failed",
                    graph=runtime.context.graph_name,
                    sessionId=runtime.context.session_id,
                    requestId=runtime.context.request_id,
                    pageId=runtime.context.page_id,
                    node="tools",
                    status="failed",
                    elapsedMs=round((time.perf_counter() - started) * 1000),
                    errorType=type(exc).__name__,
                ),
            )
            raise
        _logger.info(
            "Plan analyze tools node completed",
            extra=context_extra(
                event="graph_node_completed",
                graph=runtime.context.graph_name,
                sessionId=runtime.context.session_id,
                requestId=runtime.context.request_id,
                pageId=runtime.context.page_id,
                node="tools",
                status="completed",
                elapsedMs=round((time.perf_counter() - started) * 1000),
            ),
        )
        return payload

    # ------------------------------------------------------------------
    # 写链：记忆提交（异步入队，不阻塞主流程）
    # ------------------------------------------------------------------

    async def memory_commit(
        self,
        state: PlanAnalyzeGraphState,
        runtime: Runtime[GraphRunContext],
    ) -> dict:
        """将本轮关键信息异步入队写入 Mem0。"""
        started = time.perf_counter()
        _logger.info(
            "Memory commit node started",
            extra=context_extra(
                event="graph_node_started",
                graph=runtime.context.graph_name,
                sessionId=runtime.context.session_id,
                requestId=runtime.context.request_id,
                pageId=runtime.context.page_id,
                node="memory_commit",
                status="started",
            ),
        )
        commit_worker = runtime.context.memory_commit_worker
        if commit_worker is None:
            _logger.info(
                "Memory commit skipped",
                extra=context_extra(
                    event="memory_commit_skipped",
                    graph=runtime.context.graph_name,
                    sessionId=runtime.context.session_id,
                    requestId=runtime.context.request_id,
                    pageId=runtime.context.page_id,
                    node="memory_commit",
                    status="skipped",
                ),
            )
            result: dict = {}
            _logger.info(
                "Memory commit node completed",
                extra=context_extra(
                    event="graph_node_completed",
                    graph=runtime.context.graph_name,
                    sessionId=runtime.context.session_id,
                    requestId=runtime.context.request_id,
                    pageId=runtime.context.page_id,
                    node="memory_commit",
                    status="completed",
                    elapsedMs=round((time.perf_counter() - started) * 1000),
                ),
            )
            return result

        user_id = state.get("user_id", "")
        if not user_id:
            result = {}
            _logger.info(
                "Memory commit node completed",
                extra=context_extra(
                    event="graph_node_completed",
                    graph=runtime.context.graph_name,
                    sessionId=runtime.context.session_id,
                    requestId=runtime.context.request_id,
                    pageId=runtime.context.page_id,
                    node="memory_commit",
                    status="completed",
                    elapsedMs=round((time.perf_counter() - started) * 1000),
                ),
            )
            return result

        messages = state.get("messages", [])
        commit_messages = self._extract_commit_messages(messages)
        if not commit_messages:
            result = {}
            _logger.info(
                "Memory commit node completed",
                extra=context_extra(
                    event="graph_node_completed",
                    graph=runtime.context.graph_name,
                    sessionId=runtime.context.session_id,
                    requestId=runtime.context.request_id,
                    pageId=runtime.context.page_id,
                    node="memory_commit",
                    status="completed",
                    elapsedMs=round((time.perf_counter() - started) * 1000),
                ),
            )
            return result

        key = f"{runtime.context.trace_id}:{len(messages)}"
        await commit_worker.enqueue(user_id, commit_messages, key)
        _logger.info(
            "Memory commit requested",
            extra=context_extra(
                event="memory_commit_requested",
                graph=runtime.context.graph_name,
                sessionId=runtime.context.session_id,
                requestId=runtime.context.request_id,
                pageId=runtime.context.page_id,
                userId=user_id,
                node="memory_commit",
                status="queued",
                messageCount=len(commit_messages),
            ),
        )
        result = {}
        _logger.info(
            "Memory commit node completed",
            extra=context_extra(
                event="graph_node_completed",
                graph=runtime.context.graph_name,
                sessionId=runtime.context.session_id,
                requestId=runtime.context.request_id,
                pageId=runtime.context.page_id,
                node="memory_commit",
                status="completed",
                elapsedMs=round((time.perf_counter() - started) * 1000),
            ),
        )
        return result

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
