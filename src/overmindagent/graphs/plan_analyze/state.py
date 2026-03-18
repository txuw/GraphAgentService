from __future__ import annotations

from typing import TypedDict

from langchain_core.messages import AnyMessage
from langgraph.graph import add_messages
from typing_extensions import Annotated


class PlanAnalyzeGraphInput(TypedDict):
    query: str


class PlanAnalyzeGraphState(TypedDict, total=False):
    query: str
    messages: Annotated[list[AnyMessage], add_messages]
    plan: str
    analysis: str


class PlanAnalyzeGraphOutput(TypedDict):
    plan: str
    analysis: str
