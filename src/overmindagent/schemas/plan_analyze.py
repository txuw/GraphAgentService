from __future__ import annotations

from pydantic import BaseModel, Field


class PlanAnalyzeRequest(BaseModel):
    query: str = Field(default="")


class PlanAnalyzeOutput(BaseModel):
    plan: str = Field(default="")
    analysis: str = Field(default="")
