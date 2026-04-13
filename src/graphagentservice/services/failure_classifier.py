from __future__ import annotations

import asyncio
from collections.abc import Sequence
from typing import Any

from langchain_core.messages import AIMessage, ToolMessage
from langgraph.errors import GraphInterrupt

from graphagentservice.common.failures import (
    FailureContext,
    FailureDecision,
    FailureKind,
    RecoverableGraphError,
    RecoveryAction,
    ToolBusinessError,
    UnrecoverableGraphError,
)


class FailureClassifier:
    def classify(
        self,
        exc: Exception,
        *,
        node: str,
        messages: Sequence[Any] = (),
        context: FailureContext | None = None,
    ) -> FailureDecision:
        if isinstance(exc, GraphInterrupt):
            raise exc
        if isinstance(exc, (RecoverableGraphError, UnrecoverableGraphError)):
            return exc.decision

        failure_context = context or FailureContext(node=node)
        if self._looks_like_transient_error(exc):
            return FailureDecision(
                kind=FailureKind.TRANSIENT,
                action=RecoveryAction.WRITE_TOOL_ERROR_MESSAGE,
                message=str(exc),
                recoverable=True,
                tool_name=failure_context.tool_name,
                tool_call_id=failure_context.tool_call_id,
                retriable=True,
            )
        if self._looks_like_protocol_error(exc, messages):
            return FailureDecision(
                kind=FailureKind.PROTOCOL,
                action=RecoveryAction.REPAIR_TOOL_PROTOCOL,
                message=str(exc),
                recoverable=True,
                tool_name=failure_context.tool_name,
                tool_call_id=failure_context.tool_call_id,
            )
        if isinstance(exc, ToolBusinessError):
            return FailureDecision(
                kind=FailureKind.BUSINESS,
                action=RecoveryAction.WRITE_TOOL_ERROR_MESSAGE,
                message=str(exc),
                recoverable=True,
                tool_name=failure_context.tool_name,
                tool_call_id=failure_context.tool_call_id,
            )
        return FailureDecision(
            kind=FailureKind.FATAL,
            action=RecoveryAction.ABORT_GRAPH,
            message=str(exc),
            recoverable=False,
            tool_name=failure_context.tool_name,
            tool_call_id=failure_context.tool_call_id,
        )

    @staticmethod
    def _looks_like_transient_error(exc: Exception) -> bool:
        if isinstance(exc, (TimeoutError, asyncio.TimeoutError, ConnectionError)):
            return True
        text = str(exc).lower()
        transient_tokens = ("timeout", "timed out", "temporar", "429", "rate limit", "unavailable")
        return any(token in text for token in transient_tokens)

    @staticmethod
    def _looks_like_protocol_error(exc: Exception, messages: Sequence[Any]) -> bool:
        if _has_unclosed_tool_round(messages):
            return True
        text = str(exc).lower()
        protocol_tokens = ("tool_call", "tool call", "toolmessage", "tool message", "argument", "validation")
        return any(token in text for token in protocol_tokens)


def _has_unclosed_tool_round(messages: Sequence[Any]) -> bool:
    declared: set[str] = set()
    resolved: set[str] = set()
    for message in messages:
        if isinstance(message, AIMessage):
            for call in message.tool_calls:
                call_id = str(call.get("id", ""))
                if call_id:
                    declared.add(call_id)
        elif isinstance(message, ToolMessage):
            call_id = str(message.tool_call_id)
            if call_id:
                resolved.add(call_id)
    return bool(declared - resolved)
