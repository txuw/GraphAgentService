from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel

from overmindagent.llm.router import LLMRouter


@dataclass(frozen=True, slots=True)
class GraphRunContext:
    llm_router: LLMRouter
    graph_name: str
    llm_bindings: Mapping[str, str] = field(default_factory=dict)

    def resolve_model(
        self,
        *,
        binding: str | None = None,
        profile: str | None = None,
        tags: Sequence[str] = (),
        metadata: Mapping[str, Any] | None = None,
    ):
        resolved_profile_name = profile
        if resolved_profile_name is None and binding is not None:
            resolved_profile_name = self.llm_bindings.get(binding, binding)

        resolved_profile = self.llm_router.resolve_profile(resolved_profile_name)
        base_tags = [
            f"graph:{self.graph_name}",
            f"profile:{resolved_profile.name}",
        ]
        if binding:
            base_tags.append(f"binding:{binding}")
        base_tags.extend(tags)

        base_metadata = {
            "graph_name": self.graph_name,
            "profile": resolved_profile.name,
        }
        if binding:
            base_metadata["binding"] = binding
        if metadata:
            base_metadata.update(dict(metadata))

        return self.llm_router.create_model(
            profile=resolved_profile.name,
            tags=tuple(base_tags),
            metadata=base_metadata,
        )

    def structured_model(
        self,
        *,
        schema: type[BaseModel],
        binding: str | None = None,
        profile: str | None = None,
        method: str = "json_schema",
        tags: Sequence[str] = (),
        metadata: Mapping[str, Any] | None = None,
        **kwargs: Any,
    ):
        model = self.resolve_model(
            binding=binding,
            profile=profile,
            tags=tags,
            metadata=metadata,
        )
        return model.with_structured_output(schema, method=method, **kwargs)

    def tool_model(
        self,
        *,
        tools: Sequence[Any],
        binding: str | None = None,
        profile: str | None = None,
        tags: Sequence[str] = (),
        metadata: Mapping[str, Any] | None = None,
        **kwargs: Any,
    ):
        model = self.resolve_model(
            binding=binding,
            profile=profile,
            tags=tags,
            metadata=metadata,
        )
        return model.bind_tools(tools, **kwargs)


@dataclass(frozen=True, slots=True)
class GraphRuntime:
    name: str
    description: str
    graph: Any
    input_model: type[BaseModel]
    output_model: type[BaseModel]
    llm_bindings: Mapping[str, str] = field(default_factory=dict)
    stream_modes: tuple[str, ...] = ("updates", "messages", "custom", "values")
