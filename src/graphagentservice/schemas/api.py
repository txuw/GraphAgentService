from __future__ import annotations

from typing import Any, Generic, TypeVar

from pydantic import AliasChoices, BaseModel, ConfigDict, Field

from graphagentservice.schemas.analysis import TextAnalysisOutput
from graphagentservice.schemas.image import ImageAgentOutput
from graphagentservice.schemas.image_calories import ImageCaloriesOutput
from graphagentservice.schemas.plan_analyze import PlanAnalyzeOutput
from graphagentservice.schemas.plan_summary import PlanAnalyzeSummaryOutput
from graphagentservice.schemas.tool_agent import ToolAgentOutput

TResult = TypeVar("TResult")
TGraphOutput = TypeVar("TGraphOutput")


class ResultResponse(BaseModel, Generic[TResult]):
    code: int = Field(default=200)
    msg: str = Field(default="success")
    data: TResult


class AgentStreamEvent(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    session_id: str | None = Field(
        default=None,
        validation_alias=AliasChoices("session_id", "sessionId"),
        serialization_alias="sessionId",
    )
    request_id: str | None = Field(
        default=None,
        validation_alias=AliasChoices("request_id", "requestId"),
        serialization_alias="requestId",
    )
    trace_id: str | None = Field(
        default=None,
        validation_alias=AliasChoices("trace_id", "traceId"),
        serialization_alias="traceId",
    )
    event_type: str = Field(
        validation_alias=AliasChoices("event_type", "eventType"),
        serialization_alias="eventType",
    )
    event_id: str = Field(
        validation_alias=AliasChoices("event_id", "eventId"),
        serialization_alias="eventId",
    )
    seq: int | None = Field(default=None)
    content: str | None = Field(default=None)
    done: bool | None = Field(default=None)
    finish_reason: str | None = Field(
        default=None,
        validation_alias=AliasChoices("finish_reason", "finishReason"),
        serialization_alias="finishReason",
    )
    code: str | None = Field(default=None)
    message: str | None = Field(default=None)
    retriable: bool | None = Field(default=None)


class GraphDescriptorResponse(BaseModel):
    name: str
    description: str
    input_schema: dict[str, Any]
    output_schema: dict[str, Any]
    stream_modes: list[str] = Field(default_factory=list)


class GraphInvokeResponse(BaseModel):
    success: bool = Field(default=True)
    graph_name: str
    session_id: str | None = Field(default=None, serialization_alias="sessionId")
    data: dict[str, Any]


class TypedGraphInvokeResponse(BaseModel, Generic[TGraphOutput]):
    success: bool = Field(default=True)
    graph_name: str
    session_id: str | None = Field(default=None, serialization_alias="sessionId")
    data: TGraphOutput


class GraphStreamAcceptedResponse(BaseModel):
    success: bool = Field(default=True)
    accepted: bool = Field(default=True)
    graph_name: str
    session_id: str = Field(serialization_alias="sessionId")
    page_id: str | None = Field(default=None, serialization_alias="pageId")
    request_id: str = Field(serialization_alias="requestId")


class GraphRequestEnvelope(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    session_id: str | None = Field(
        default=None,
        validation_alias=AliasChoices("session_id", "sessionId"),
        serialization_alias="sessionId",
    )
    request_id: str | None = Field(
        default=None,
        validation_alias=AliasChoices("request_id", "requestId"),
        serialization_alias="requestId",
    )
    page_id: str | None = Field(
        default=None,
        validation_alias=AliasChoices("page_id", "pageId"),
        serialization_alias="pageId",
    )

    def graph_payload(self) -> dict[str, Any]:
        payload = self.model_dump(
            exclude={"session_id", "request_id", "page_id"},
            by_alias=False,
        )
        return {str(key): value for key, value in payload.items()}


class TextAnalysisGraphRequest(GraphRequestEnvelope):
    text: str = Field(default="", validation_alias=AliasChoices("text", "message"))


class PlanAnalyzeGraphRequest(GraphRequestEnvelope):
    query: str = Field(default="", validation_alias=AliasChoices("query", "message"))


class PlanAnalyzeSummaryRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    session_id: str = Field(
        validation_alias=AliasChoices("session_id", "sessionId"),
        serialization_alias="sessionId",
    )


class ToolAgentGraphRequest(GraphRequestEnvelope):
    query: str = Field(default="", validation_alias=AliasChoices("query", "message"))


class ImageAgentGraphRequest(GraphRequestEnvelope):
    text: str = Field(
        default="",
        validation_alias=AliasChoices("text", "message", "description"),
    )
    image_url: str = Field(
        min_length=1,
        validation_alias=AliasChoices("image_url", "imageUrl"),
        serialization_alias="imageUrl",
    )


class ImageCaloriesGraphRequest(GraphRequestEnvelope):
    text: str = Field(
        default="",
        validation_alias=AliasChoices("text", "message", "description"),
    )
    image_url: str = Field(
        min_length=1,
        validation_alias=AliasChoices("image_url", "imageUrl"),
        serialization_alias="imageUrl",
    )


class ChatExecuteRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    graph_name: str = Field(
        validation_alias=AliasChoices("graph_name", "graphName"),
    )
    input: dict[str, Any] = Field(default_factory=dict)
    session_id: str = Field(
        validation_alias=AliasChoices("session_id", "sessionId"),
        serialization_alias="sessionId",
    )
    page_id: str = Field(
        validation_alias=AliasChoices("page_id", "pageId"),
        serialization_alias="pageId",
    )
    request_id: str | None = Field(
        default=None,
        validation_alias=AliasChoices("request_id", "requestId"),
        serialization_alias="requestId",
    )


class ChatExecuteResponse(BaseModel):
    success: bool = Field(default=True)
    accepted: bool = Field(default=True)
    graph_name: str
    session_id: str = Field(serialization_alias="sessionId")
    page_id: str | None = Field(default=None, serialization_alias="pageId")
    request_id: str = Field(serialization_alias="requestId")


class ChatExecuteRequestBase(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    session_id: str = Field(
        validation_alias=AliasChoices("session_id", "sessionId"),
        serialization_alias="sessionId",
    )
    page_id: str = Field(
        validation_alias=AliasChoices("page_id", "pageId"),
        serialization_alias="pageId",
    )
    request_id: str | None = Field(
        default=None,
        validation_alias=AliasChoices("request_id", "requestId"),
        serialization_alias="requestId",
    )


class TextAnalysisChatExecuteRequest(ChatExecuteRequestBase):
    text: str = Field(default="", validation_alias=AliasChoices("text", "message"))

    def graph_payload(self) -> dict[str, Any]:
        return {"text": self.text}


class PlanAnalyzeChatExecuteRequest(ChatExecuteRequestBase):
    query: str = Field(default="", validation_alias=AliasChoices("query", "message"))

    def graph_payload(self) -> dict[str, Any]:
        return {"query": self.query}


class ToolAgentChatExecuteRequest(ChatExecuteRequestBase):
    query: str = Field(default="", validation_alias=AliasChoices("query", "message"))

    def graph_payload(self) -> dict[str, Any]:
        return {"query": self.query}


class ImageAgentChatExecuteRequest(ChatExecuteRequestBase):
    text: str = Field(
        default="",
        validation_alias=AliasChoices("text", "message", "description"),
    )
    image_url: str = Field(
        min_length=1,
        validation_alias=AliasChoices("image_url", "imageUrl"),
        serialization_alias="imageUrl",
    )

    def graph_payload(self) -> dict[str, Any]:
        return {"text": self.text, "image_url": self.image_url}


class ImageCaloriesChatExecuteRequest(ChatExecuteRequestBase):
    text: str = Field(
        default="",
        validation_alias=AliasChoices("text", "message", "description"),
    )
    image_url: str = Field(
        min_length=1,
        validation_alias=AliasChoices("image_url", "imageUrl"),
        serialization_alias="imageUrl",
    )

    def graph_payload(self) -> dict[str, Any]:
        return {"text": self.text, "image_url": self.image_url}


GraphInvokeResult = ResultResponse[dict[str, Any]]
TextAnalysisInvokeResult = ResultResponse[TextAnalysisOutput]
PlanAnalyzeInvokeResult = ResultResponse[PlanAnalyzeOutput]
PlanAnalyzeSummaryInvokeResult = ResultResponse[PlanAnalyzeSummaryOutput]
ToolAgentInvokeResult = ResultResponse[ToolAgentOutput]
ImageAgentInvokeResult = ResultResponse[ImageAgentOutput]
ImageCaloriesInvokeResult = ResultResponse[ImageCaloriesOutput]
