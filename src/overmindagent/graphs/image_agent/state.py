from __future__ import annotations

from typing import TypedDict


class ImageGraphInput(TypedDict):
    text: str
    image_url: str


class ImageGraphState(TypedDict, total=False):
    text: str
    image_url: str
    answer: str


class ImageGraphOutput(TypedDict):
    answer: str
