from __future__ import annotations

import json
from collections.abc import Sequence
from typing import Any

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage, ToolMessage

from graphagentservice.graphs.runtime import GraphRunContext
from graphagentservice.llm import LLMRouter
from graphagentservice.schemas.plan_summary import PlanAnalyzeSummaryOutput, PlanSummary

from .graph_service import GraphRequestContext, GraphService

PLAN_SUMMARY_SYSTEM_PROMPT = """
# 角色定位
你是健身计划总结助手。你的任务是基于当前会话历史中已生成的健身计划内容，
产出结构化 JSON 训练计划，方便前端直接渲染。

# 输出规则（必须严格遵守）
1. 只允许输出 JSON 对象，不要输出任何解释、标记、代码块或 Markdown。
2. 字段必须严格符合 Schema，禁止新增或缺失字段。
3. 数值字段必须为数字类型，不要加单位或字符串。
4. 列表字段为空时输出 []，对象字段不存在时输出空对象 {}。
5. 文本字段用中文，动作名与标题应简洁清晰。
6. 优先使用会话中的既有信息；若缺失，合理补全但不要杜撰具体数值过细。
""".strip()

PLAN_SUMMARY_PROMPT_TEMPLATE = """
请基于下面的 plan-analyze checkpoint state 总结训练计划，并严格输出符合 Schema 的 JSON 对象。

Schema:
{schema}

Checkpoint State:
{state_summary}
""".strip()


class PlanAnalyzeSummaryStateError(ValueError):
    pass


class PlanAnalyzeSummaryService:
    def __init__(
        self,
        graph_service: GraphService,
        llm_router: LLMRouter,
    ) -> None:
        self._graph_service = graph_service
        self._llm_router = llm_router

    async def summarize(
        self,
        *,
        session_id: str,
        request_context: GraphRequestContext,
    ) -> PlanAnalyzeSummaryOutput:
        state = await self._graph_service.get_latest_state(
            "plan-analyze",
            session_id=session_id,
        )
        summary_payload = self._build_summary_payload(state)
        if not self._has_meaningful_content(summary_payload):
            raise PlanAnalyzeSummaryStateError(
                "Checkpoint state does not contain enough content to summarize."
            )

        runtime_context = GraphRunContext(
            llm_router=self._llm_router,
            graph_name="plan-analyze-summary",
            current_user=request_context.current_user,
            trace_id=request_context.trace_id,
            request_headers=request_context.request_headers,
        )
        model = runtime_context.structured_model_with_json_object(
            schema=PlanSummary,
            profile="default",
            tags=("summary", "structured-output"),
            metadata={"source_graph": "plan-analyze"},
        )
        plan_summary = await model.ainvoke(self._build_messages(summary_payload))
        return PlanAnalyzeSummaryOutput(
            analysis=str(state.get("analysis", "") or "").strip(),
            plan_summary=plan_summary,
        )

    @staticmethod
    def _build_messages(summary_payload: dict[str, object]) -> list[object]:
        return [
            SystemMessage(content=PLAN_SUMMARY_SYSTEM_PROMPT),
            HumanMessage(
                content=PLAN_SUMMARY_PROMPT_TEMPLATE.format(
                    schema=json.dumps(PlanSummary.model_json_schema(), ensure_ascii=False, indent=2),
                    state_summary=json.dumps(summary_payload, ensure_ascii=False, indent=2),
                )
            ),
        ]

    def _build_summary_payload(self, state: dict[str, object]) -> dict[str, object]:
        return {
            "query": str(state.get("query", "") or "").strip(),
            "plan": str(state.get("plan", "") or "").strip(),
            "analysis": str(state.get("analysis", "") or "").strip(),
            "messages": self._messages_to_transcript(state.get("messages")),
        }

    def _has_meaningful_content(self, summary_payload: dict[str, object]) -> bool:
        return any(
            isinstance(value, str) and value.strip()
            for value in summary_payload.values()
        )

    def _messages_to_transcript(self, raw_messages: object) -> str:
        if not isinstance(raw_messages, Sequence) or isinstance(raw_messages, (str, bytes)):
            return ""

        lines: list[str] = []
        for message in raw_messages:
            role = self._message_role(message)
            content = self._message_content(message)
            if not content:
                continue
            lines.append(f"{role}: {content}")
        return "\n".join(lines).strip()

    @staticmethod
    def _message_role(message: object) -> str:
        if isinstance(message, HumanMessage):
            return "user"
        if isinstance(message, AIMessage):
            return "assistant"
        if isinstance(message, ToolMessage):
            tool_name = getattr(message, "name", "") or "tool"
            return f"tool:{tool_name}"
        if isinstance(message, SystemMessage):
            return "system"
        if isinstance(message, BaseMessage):
            return str(getattr(message, "type", "message"))
        if isinstance(message, dict):
            message_type = message.get("type") or message.get("role") or "message"
            return str(message_type)
        return "message"

    @classmethod
    def _message_content(cls, message: object) -> str:
        if isinstance(message, BaseMessage):
            return cls._content_to_text(message.content)
        if isinstance(message, dict):
            if "content" in message:
                return cls._content_to_text(message["content"])
            if "data" in message:
                return cls._content_to_text(message["data"])
        return cls._content_to_text(message)

    @staticmethod
    def _content_to_text(content: Any) -> str:
        if isinstance(content, str):
            return content.strip()
        if isinstance(content, list):
            parts: list[str] = []
            for block in content:
                if isinstance(block, dict):
                    text = block.get("text")
                    if text is not None:
                        parts.append(str(text))
                        continue
                parts.append(str(block))
            return "\n".join(part for part in parts if part).strip()
        if isinstance(content, dict):
            return json.dumps(content, ensure_ascii=False, sort_keys=True)
        return str(content).strip()
