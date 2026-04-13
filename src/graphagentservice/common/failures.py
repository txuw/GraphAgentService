from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class FailureKind(StrEnum):
    TRANSIENT = "transient"
    PROTOCOL = "protocol"
    BUSINESS = "business"
    FATAL = "fatal"


class RecoveryAction(StrEnum):
    WRITE_TOOL_ERROR_MESSAGE = "write_tool_error_message"
    REPAIR_TOOL_PROTOCOL = "repair_tool_protocol"
    TRIM_TO_STABLE_BOUNDARY = "trim_to_stable_boundary"
    ABORT_GRAPH = "abort_graph"


class RunStatus(StrEnum):
    CLEAN = "clean"
    RECOVERING = "recovering"
    RECOVERABLE_FAILED = "recoverable_failed"
    UNRECOVERABLE_FAILED = "unrecoverable_failed"


@dataclass(slots=True)
class FailureContext:
    node: str
    tool_name: str = ""
    tool_call_id: str = ""
    detail: str = ""


@dataclass(slots=True)
class FailureDecision:
    kind: FailureKind
    action: RecoveryAction
    message: str
    recoverable: bool
    tool_name: str = ""
    tool_call_id: str = ""
    retriable: bool = False


@dataclass(slots=True)
class RecoveryFailureRecord:
    kind: str = ""
    message: str = ""
    node: str = ""
    tool_name: str = ""
    tool_call_id: str = ""
    retriable: bool = False

    def to_dict(self) -> dict[str, object]:
        return {
            "kind": self.kind,
            "message": self.message,
            "node": self.node,
            "tool_name": self.tool_name,
            "tool_call_id": self.tool_call_id,
            "retriable": self.retriable,
        }


@dataclass(slots=True)
class GraphRecoveryState:
    run_status: str = RunStatus.CLEAN.value
    last_stable_message_count: int = 0
    recovery_attempts: int = 0
    pending_actions: list[str] = field(default_factory=list)
    last_failure: RecoveryFailureRecord | None = None

    def to_dict(self) -> dict[str, object]:
        payload: dict[str, object] = {
            "run_status": self.run_status,
            "last_stable_message_count": self.last_stable_message_count,
            "recovery_attempts": self.recovery_attempts,
            "pending_actions": list(self.pending_actions),
        }
        if self.last_failure is not None:
            payload["last_failure"] = self.last_failure.to_dict()
        return payload

    @classmethod
    def from_mapping(cls, value: Any) -> GraphRecoveryState:
        if not isinstance(value, dict):
            return cls()
        last_failure_payload = value.get("last_failure")
        last_failure = None
        if isinstance(last_failure_payload, dict):
            last_failure = RecoveryFailureRecord(
                kind=str(last_failure_payload.get("kind", "")),
                message=str(last_failure_payload.get("message", "")),
                node=str(last_failure_payload.get("node", "")),
                tool_name=str(last_failure_payload.get("tool_name", "")),
                tool_call_id=str(last_failure_payload.get("tool_call_id", "")),
                retriable=bool(last_failure_payload.get("retriable", False)),
            )
        pending_actions_payload = value.get("pending_actions", [])
        pending_actions = [
            str(action)
            for action in pending_actions_payload
            if isinstance(action, str) and action
        ]
        return cls(
            run_status=str(value.get("run_status", RunStatus.CLEAN.value)),
            last_stable_message_count=max(
                int(value.get("last_stable_message_count", 0) or 0),
                0,
            ),
            recovery_attempts=max(int(value.get("recovery_attempts", 0) or 0), 0),
            pending_actions=pending_actions,
            last_failure=last_failure,
        )

    def with_failure(self, decision: FailureDecision, *, node: str) -> GraphRecoveryState:
        self.run_status = (
            RunStatus.RECOVERABLE_FAILED.value
            if decision.recoverable
            else RunStatus.UNRECOVERABLE_FAILED.value
        )
        self.pending_actions = [decision.action.value]
        self.last_failure = RecoveryFailureRecord(
            kind=decision.kind.value,
            message=decision.message,
            node=node,
            tool_name=decision.tool_name,
            tool_call_id=decision.tool_call_id,
            retriable=decision.retriable,
        )
        return self


class GraphFailure(Exception):
    def __init__(
        self,
        *,
        decision: FailureDecision,
        node: str,
    ) -> None:
        super().__init__(decision.message)
        self.decision = decision
        self.node = node


class RecoverableGraphError(GraphFailure):
    pass


class UnrecoverableGraphError(GraphFailure):
    pass


class ToolBusinessError(Exception):
    pass
