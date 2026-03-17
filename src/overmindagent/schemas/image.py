from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class ImageAgentRequest(BaseModel):
    text: str = Field(default="")
    image_url: str = Field(default="")


class ImageAgentOutput(BaseModel):
    answer: str = Field(default="")
