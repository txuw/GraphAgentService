from __future__ import annotations

from typing import TypedDict

from graphagentservice.schemas.body_report import BodyReportInfo


class BodyReportAnalyzeGraphInput(TypedDict):
    text: str
    image_url: str


class BodyReportAnalyzeGraphState(TypedDict, total=False):
    text: str
    image_url: str
    answer: BodyReportInfo


class BodyReportAnalyzeGraphOutput(TypedDict):
    answer: BodyReportInfo
