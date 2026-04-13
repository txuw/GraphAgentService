from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from langchain_core.messages import AIMessage, AnyMessage, ToolMessage

from graphagentservice.common.failures import (
    FailureDecision,
    FailureKind,
    GraphRecoveryState,
    RunStatus,
)


@dataclass(slots=True)
class RepairResult:
    messages: list[AnyMessage]
    recovery: GraphRecoveryState
    mutated: bool
    can_continue: bool


class StateRepairService:
    def repair(
        self,
        *,
        messages: Sequence[AnyMessage],
        recovery: GraphRecoveryState | None,
        failure: FailureDecision | None = None,
    ) -> RepairResult:
        patched_messages = list(messages)
        patched_recovery = recovery or GraphRecoveryState()
        mutated = False
        stable_count = self._resolve_stable_count(patched_messages, patched_recovery)
        declared, results = _collect_tool_protocol(patched_messages)

        duplicate_indexes = _find_duplicate_tool_message_indexes(patched_messages)
        if duplicate_indexes:
            patched_messages = [
                message
                for index, message in enumerate(patched_messages)
                if index not in duplicate_indexes
            ]
            mutated = True
            declared, results = _collect_tool_protocol(patched_messages)

        orphan_indexes = _find_orphan_tool_message_indexes(patched_messages)
        if orphan_indexes:
            patched_messages = [
                message
                for index, message in enumerate(patched_messages)
                if index not in orphan_indexes
            ]
            mutated = True
            declared, results = _collect_tool_protocol(patched_messages)

        missing_ids = [call_id for call_id in declared if call_id not in results]
        if missing_ids:
            if failure is not None and failure.kind != FailureKind.FATAL:
                for call_id in missing_ids:
                    patched_messages.append(
                        build_error_tool_message(
                            tool_call_id=call_id,
                            tool_name=_resolve_tool_name(patched_messages, call_id),
                            decision=failure,
                        )
                    )
                mutated = True
            else:
                trimmed = patched_messages[:stable_count]
                mutated = mutated or len(trimmed) != len(patched_messages)
                patched_messages = trimmed

        patched_recovery.last_stable_message_count = self._resolve_stable_count(
            patched_messages,
            patched_recovery,
        )
        patched_recovery.pending_actions = []
        patched_recovery.run_status = RunStatus.CLEAN.value
        patched_recovery.recovery_attempts += 1
        return RepairResult(
            messages=patched_messages,
            recovery=patched_recovery,
            mutated=mutated,
            can_continue=True,
        )

    @staticmethod
    def _resolve_stable_count(
        messages: Sequence[AnyMessage],
        recovery: GraphRecoveryState,
    ) -> int:
        if 0 <= recovery.last_stable_message_count <= len(messages):
            return recovery.last_stable_message_count
        declared: set[str] = set()
        resolved: set[str] = set()
        stable_count = 0
        for index, message in enumerate(messages, start=1):
            if isinstance(message, AIMessage):
                for call in message.tool_calls:
                    call_id = str(call.get("id", ""))
                    if call_id:
                        declared.add(call_id)
            elif isinstance(message, ToolMessage):
                call_id = str(message.tool_call_id)
                if call_id:
                    resolved.add(call_id)
            if declared.issubset(resolved):
                stable_count = index
        return stable_count


def build_error_tool_message(
    *,
    tool_call_id: str,
    tool_name: str,
    decision: FailureDecision,
) -> ToolMessage:
    payload = json.dumps(
        {
            "error": decision.message,
            "failure_kind": decision.kind.value,
            "tool_name": tool_name,
            "retriable": decision.retriable,
        },
        ensure_ascii=True,
    )
    return ToolMessage(
        content=payload,
        tool_call_id=tool_call_id,
        status="error",
    )


def _collect_tool_protocol(
    messages: Sequence[AnyMessage],
) -> tuple[list[str], dict[str, ToolMessage]]:
    declared: list[str] = []
    results: dict[str, ToolMessage] = {}
    for message in messages:
        if isinstance(message, AIMessage):
            for call in message.tool_calls:
                call_id = str(call.get("id", ""))
                if call_id:
                    declared.append(call_id)
        elif isinstance(message, ToolMessage):
            call_id = str(message.tool_call_id)
            if call_id:
                results[call_id] = message
    return declared, results


def _find_duplicate_tool_message_indexes(messages: Sequence[AnyMessage]) -> set[int]:
    latest_by_call_id: dict[str, int] = {}
    duplicate_indexes: set[int] = set()
    for index, message in enumerate(messages):
        if not isinstance(message, ToolMessage):
            continue
        call_id = str(message.tool_call_id)
        if not call_id:
            continue
        previous_index = latest_by_call_id.get(call_id)
        if previous_index is not None:
            previous = messages[previous_index]
            if isinstance(previous, ToolMessage) and previous.status == "error" and message.status != "error":
                duplicate_indexes.add(previous_index)
                latest_by_call_id[call_id] = index
            else:
                duplicate_indexes.add(index)
                continue
        else:
            latest_by_call_id[call_id] = index
    return duplicate_indexes


def _find_orphan_tool_message_indexes(messages: Sequence[AnyMessage]) -> set[int]:
    declared: set[str] = set()
    orphan_indexes: set[int] = set()
    for index, message in enumerate(messages):
        if isinstance(message, AIMessage):
            for call in message.tool_calls:
                call_id = str(call.get("id", ""))
                if call_id:
                    declared.add(call_id)
        elif isinstance(message, ToolMessage):
            call_id = str(message.tool_call_id)
            if call_id and call_id not in declared:
                orphan_indexes.add(index)
    return orphan_indexes


def _resolve_tool_name(messages: Sequence[AnyMessage], tool_call_id: str) -> str:
    for message in reversed(messages):
        if not isinstance(message, AIMessage):
            continue
        for call in message.tool_calls:
            if str(call.get("id", "")) == tool_call_id:
                return str(call.get("name", ""))
    return ""
