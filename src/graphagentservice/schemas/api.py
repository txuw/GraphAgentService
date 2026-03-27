from __future__ import annotations

from typing import Any, Generic, TypeVar

from pydantic import AliasChoices, BaseModel, ConfigDict, Field

from graphagentservice.schemas.analysis import TextAnalysisRequest
from graphagentservice.schemas.image import ImageAgentRequest
from graphagentservice.schemas.image_calories import ImageCaloriesRequest
from graphagentservice.schemas.plan_analyze import PlanAnalyzeRequest
from graphagentservice.schemas.tool_agent import ToolAgentRequest

TGraphOutput = TypeVar("TGraphOutput")


class GraphInvokeResponse(BaseModel):
    success: bool = Field(default=True)
    graph_name: str
    session_id: str | None = Field(default=None)
    data: dict[str, Any]


class TypedGraphInvokeResponse(BaseModel, Generic[TGraphOutput]):
    success: bool = Field(default=True)
    graph_name: str
    session_id: str | None = Field(default=None)
    data: TGraphOutput


class GraphDescriptorResponse(BaseModel):
    name: str
    description: str
    input_schema: dict[str, Any]
    output_schema: dict[str, Any]
    stream_modes: list[str] = Field(default_factory=list)


class ChatExecuteRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    graph_name: str = Field(
        validation_alias=AliasChoices("graph_name", "graphName"),
    )
    input: dict[str, Any] = Field(default_factory=dict)
    session_id: str = Field(
        validation_alias=AliasChoices("session_id", "sessionId"),
    )
    page_id: str = Field(
        validation_alias=AliasChoices("page_id", "pageId"),
    )
    request_id: str | None = Field(
        default=None,
        validation_alias=AliasChoices("request_id", "requestId"),
    )


class ChatExecuteResponse(BaseModel):
    success: bool = Field(default=True)
    accepted: bool = Field(default=True)
    graph_name: str
    session_id: str
    page_id: str
    request_id: str


class GraphStreamAcceptedResponse(BaseModel):
    success: bool = Field(default=True)
    accepted: bool = Field(default=True)
    graph_name: str
    session_id: str
    page_id: str
    request_id: str


class ChatExecuteRequestBase(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    session_id: str = Field(
        validation_alias=AliasChoices("session_id", "sessionId"),
    )
    page_id: str = Field(
        validation_alias=AliasChoices("page_id", "pageId"),
    )
    request_id: str | None = Field(
        default=None,
        validation_alias=AliasChoices("request_id", "requestId"),
    )


class TextAnalysisChatExecuteRequest(ChatExecuteRequestBase, TextAnalysisRequest):
    pass


class PlanAnalyzeChatExecuteRequest(ChatExecuteRequestBase, PlanAnalyzeRequest):
    pass


class ToolAgentChatExecuteRequest(ChatExecuteRequestBase, ToolAgentRequest):
    pass


class ImageAgentChatExecuteRequest(ChatExecuteRequestBase, ImageAgentRequest):
    pass


class ImageCaloriesChatExecuteRequest(ChatExecuteRequestBase, ImageCaloriesRequest):
    pass
