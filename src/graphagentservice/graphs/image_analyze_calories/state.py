from __future__ import annotations

from typing import TypedDict

from graphagentservice.schemas import CalorieInfo


class ImageAnalyzeCaloriesGraphInput(TypedDict):
    text: str
    image_url: str


class ImageAnalyzeCaloriesGraphState(TypedDict, total=False):
    text: str
    image_url: str
    answer: CalorieInfo


class ImageAnalyzeCaloriesGraphOutput(TypedDict):
    text: str
    image_url: str
    answer: CalorieInfo
