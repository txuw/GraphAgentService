from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from langchain_core.messages import AIMessage

from graphagentservice.graphs.runtime import (
    GraphRunContext,
    _supports_json_schema_response_format,
)
from graphagentservice.llm.profile import LLMProfile
from graphagentservice.schemas.image_calories import CalorieInfo


VALID_CALORIES_JSON = """
```json
{
  "foods": [
    {
      "name": "白米饭",
      "weight": 250,
      "calories": 290,
      "protein": 5.8,
      "fat": 0.8,
      "carbohydrate": 64.5
    }
  ],
  "total_calories": 290,
  "total_protein": 5.8,
  "total_fat": 0.8,
  "total_carbohydrate": 64.5
}
```
""".strip()


REPAIRED_CALORIES_JSON = """
{
  "foods": [
    {
      "name": "白米饭",
      "weight": 250,
      "calories": 290,
      "protein": 5.8,
      "fat": 0.8,
      "carbohydrate": 64.5
    },
    {
      "name": "卤肉",
      "weight": 120,
      "calories": 420,
      "protein": 16.8,
      "fat": 36,
      "carbohydrate": 3.6
    }
  ],
  "total_calories": 710,
  "total_protein": 22.6,
  "total_fat": 36.8,
  "total_carbohydrate": 68.1
}
""".strip()


@dataclass
class _FakeModelState:
    responses: list[str]
    calls: list[tuple[dict[str, Any] | None, Any]] = field(default_factory=list)
    binds: list[dict[str, Any]] = field(default_factory=list)


class _FakeChatModel:
    def __init__(
        self,
        state: _FakeModelState,
        *,
        bound_kwargs: dict[str, Any] | None = None,
    ) -> None:
        self._state = state
        self._bound_kwargs = bound_kwargs

    def bind(self, **kwargs: Any) -> _FakeChatModel:
        self._state.binds.append(kwargs)
        return _FakeChatModel(self._state, bound_kwargs=kwargs)

    async def ainvoke(self, messages: Any) -> AIMessage:
        self._state.calls.append((self._bound_kwargs, messages))
        return AIMessage(content=self._state.responses.pop(0))


class _FakeRouter:
    def __init__(self, *, model_name: str, fake_model: _FakeChatModel) -> None:
        self._profile = LLMProfile(name="mult_model", model=model_name)
        self._model = fake_model

    def resolve_profile(self, profile: str | None = None) -> LLMProfile:
        return self._profile

    def create_model(self, **_: Any) -> _FakeChatModel:
        return self._model


def _context_for_qwen(state: _FakeModelState) -> GraphRunContext:
    return GraphRunContext(
        llm_router=_FakeRouter(
            model_name="litellm_proxy/qwen3.7-plus",
            fake_model=_FakeChatModel(state),
        ),
        graph_name="image-analyze-calories",
        llm_bindings={"analysis": "mult_model"},
    )


def test_qwen_profile_does_not_use_json_schema_response_format() -> None:
    profile = LLMProfile(name="mult_model", model="openai/qwen3.7-plus")

    assert _supports_json_schema_response_format(profile) is False


def test_qwen_multimodal_valid_json_does_not_trigger_repair() -> None:
    state = _FakeModelState(responses=[VALID_CALORIES_JSON])
    model = _context_for_qwen(state).structured_model_with_json_object(
        schema=CalorieInfo,
        binding="analysis",
        tags=("multimodal",),
    )

    result = asyncio.run(model.ainvoke(["image message"]))

    assert result.foods[0].name == "白米饭"
    assert result.total_calories == Decimal("290")
    assert len(state.calls) == 1
    assert state.calls[0][0] is None


def test_qwen_multimodal_invalid_json_triggers_json_mode_repair() -> None:
    state = _FakeModelState(
        responses=[
            "识别结果：白米饭约250克，卤肉约120克，总热量约710千卡。",
            REPAIRED_CALORIES_JSON,
        ]
    )
    model = _context_for_qwen(state).structured_model_with_json_object(
        schema=CalorieInfo,
        binding="analysis",
        tags=("multimodal",),
    )

    result = asyncio.run(model.ainvoke(["image message"]))

    assert [food.name for food in result.foods] == ["白米饭", "卤肉"]
    assert result.total_calories == Decimal("710")
    assert len(state.calls) == 2

    repair_kwargs, repair_messages = state.calls[1]
    assert repair_kwargs == {
        "response_format": {"type": "json_object"},
        "extra_body": {"enable_thinking": False},
    }
    assert "JSON" in repair_messages[0].content
    assert "JSON schema" in repair_messages[1].content
