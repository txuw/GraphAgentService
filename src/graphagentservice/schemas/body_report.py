from __future__ import annotations

from decimal import Decimal
from typing import Any

from pydantic import BaseModel, Field


class BodyReportRequest(BaseModel):
    text: str = Field(default="")
    image_url: str = Field(min_length=1)


class BodyReportInfo(BaseModel):
    measuredAt: str | None = Field(default=None)
    weight: Decimal | None = Field(default=None)
    fatMass: Decimal | None = Field(default=None)
    bodyFatRate: Decimal | None = Field(default=None)
    waterMass: Decimal | None = Field(default=None)
    proteinMass: Decimal | None = Field(default=None)
    skeletalMuscleMass: Decimal | None = Field(default=None)
    muscleMass: Decimal | None = Field(default=None)
    boneMass: Decimal | None = Field(default=None)
    basalMetabolism: int | None = Field(default=None)
    bmi: Decimal | None = Field(default=None)
    score: Decimal | None = Field(default=None)
    subcutaneousFatRate: Decimal | None = Field(default=None)
    fatFreeMass: Decimal | None = Field(default=None)
    muscleRate: Decimal | None = Field(default=None)
    parseConfidence: Decimal | None = Field(default=None)
    reviewRequired: bool | None = Field(default=None)
    rawResult: dict[str, Any] = Field(default_factory=dict)


class BodyReportOutput(BaseModel):
    answer: BodyReportInfo
