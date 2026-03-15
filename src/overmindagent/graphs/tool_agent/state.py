from __future__ import annotations

from typing import TypedDict

from langchain_core.messages import AnyMessage
from langgraph.graph import add_messages
from typing_extensions import Annotated

from overmindagent.schemas.tool_agent import ToolCallTrace


class ToolAgentGraphInput(TypedDict):
    query: str


class ToolAgentGraphState(TypedDict, total=False):
    query: str
    messages: Annotated[list[AnyMessage], add_messages]
    answer: str
    tools_used: list[ToolCallTrace]


class ToolAgentGraphOutput(TypedDict):
    answer: str
    tools_used: list[ToolCallTrace]
