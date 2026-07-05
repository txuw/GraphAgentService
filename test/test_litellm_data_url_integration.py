from __future__ import annotations

import asyncio
import base64
import os
from io import BytesIO

import pytest
from langchain_core.messages import HumanMessage
from PIL import Image

from graphagentservice.common.config import get_settings
from graphagentservice.llm import LLMRouter


@pytest.mark.skipif(
    os.environ.get("RUN_LITELLM_INTEGRATION") != "1",
    reason="set RUN_LITELLM_INTEGRATION=1 to call the real LiteLLM gateway",
)
def test_litellm_mult_model_accepts_image_data_url() -> None:
    asyncio.run(_assert_litellm_mult_model_accepts_image_data_url())


async def _assert_litellm_mult_model_accepts_image_data_url() -> None:
    settings = get_settings()
    profile = settings.llm.profiles.mult_model
    if not profile.api_key or not profile.base_url:
        pytest.skip("mult_model api_key/base_url is not configured")

    image_bytes = _one_pixel_jpeg()
    data_url = "data:image/jpeg;base64," + base64.b64encode(image_bytes).decode("ascii")
    model = LLMRouter(settings.llm).create_model(profile="mult_model")

    response = await model.ainvoke(
        [
            HumanMessage(
                content=[
                    {"type": "text", "text": "Describe the image in one sentence."},
                    {"type": "image_url", "image_url": {"url": data_url}},
                ]
            )
        ]
    )

    assert str(getattr(response, "content", response)).strip()


def _one_pixel_jpeg() -> bytes:
    buffer = BytesIO()
    Image.new("RGB", (1, 1), color=(128, 128, 128)).save(buffer, format="JPEG")
    return buffer.getvalue()
