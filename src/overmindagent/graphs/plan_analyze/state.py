from __future__ import annotations

from typing import TypedDict


class PlanAnalyzeGraphInput(TypedDict):
    query: str


class PlanAnalyzeGraphState(TypedDict, total=False):
    query: str
    plan: str
    analysis: str


class PlanAnalyzeGraphOutput(TypedDict):
    plan: str
    analysis: str
