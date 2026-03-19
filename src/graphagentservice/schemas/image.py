from __future__ import annotations

from pydantic import BaseModel, Field


class ImageAgentRequest(BaseModel):
    text: str = Field(default="")
    image_url: str = Field(min_length=1)


class ImageAgentOutput(BaseModel):
    answer: str = Field(default="")
