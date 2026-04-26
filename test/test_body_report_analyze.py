from __future__ import annotations

from decimal import Decimal

from graphagentservice.api.routes.graphs import _normalize_graph_payload
from graphagentservice.graphs.registry import create_graph_registry
from graphagentservice.schemas.api import BodyReportGraphRequest
from graphagentservice.schemas.body_report import BodyReportInfo, BodyReportOutput


def test_body_report_output_matches_save_request_contract() -> None:
    report = BodyReportInfo.model_validate(
        {
            "measuredAt": "2026-04-26T10:45:00Z",
            "weight": 86.3,
            "fatMass": 22.27,
            "bodyFatRate": 25.8,
            "waterMass": 46.69,
            "proteinMass": 12.6,
            "skeletalMuscleMass": 34.61,
            "muscleMass": 59.7,
            "boneMass": 4.7,
            "basalMetabolism": 1752,
            "bmi": 28.2,
            "score": 74.1,
            "subcutaneousFatRate": 23.2,
            "fatFreeMass": 64,
            "muscleRate": 69.2,
            "parseConfidence": 0.92,
            "reviewRequired": False,
            "rawResult": {"subjectName": "txuw", "bodyAge": 38, "visceralFatLevel": 8},
        }
    )

    dumped = BodyReportOutput(answer=report).model_dump()

    assert dumped["answer"]["bodyFatRate"] == Decimal("25.8")
    assert dumped["answer"]["basalMetabolism"] == 1752
    assert dumped["answer"]["parseConfidence"] == Decimal("0.92")
    assert dumped["answer"]["reviewRequired"] is False
    assert "recordBodyReportId" not in dumped["answer"]
    assert "record_body_report_id" not in dumped["answer"]
    assert "subjectName" not in dumped["answer"]


def test_body_report_graph_request_accepts_frontend_aliases() -> None:
    request = BodyReportGraphRequest.model_validate(
        {
            "imageUrl": "https://example.test/body-report.png",
            "message": "extract latest body report",
            "sessionId": "session-1",
            "pageId": "page-1",
            "requestId": "request-1",
        }
    )

    assert request.session_id == "session-1"
    assert request.page_id == "page-1"
    assert request.request_id == "request-1"
    assert request.graph_payload() == {
        "text": "extract latest body report",
        "image_url": "https://example.test/body-report.png",
    }


def test_generic_graph_payload_normalizes_body_report_aliases() -> None:
    payload, session_id, request_id, page_id = _normalize_graph_payload(
        "body-report-analyze",
        {
            "imageUrl": "https://example.test/body-report.png",
            "description": "extract latest body report",
            "sessionId": "session-1",
            "pageId": "page-1",
            "requestId": "request-1",
        },
    )

    assert payload == {
        "text": "extract latest body report",
        "image_url": "https://example.test/body-report.png",
    }
    assert session_id == "session-1"
    assert request_id == "request-1"
    assert page_id == "page-1"


def test_body_report_graph_is_registered() -> None:
    class DummySettings:
        def get(self, key: str, default=None):  # type: ignore[no-untyped-def]
            if key == "graphs":
                return {"body_report_analyze": {"llm_bindings": {"analysis": "multimodal"}}}
            return default

    class DummyCheckpointProvider:
        def build(self):  # type: ignore[no-untyped-def]
            return None

    registry = create_graph_registry(DummySettings(), DummyCheckpointProvider())
    runtime = registry.get("body-report-analyze")

    assert "body-report-analyze" in registry.list_names()
    assert runtime.input_model is not None
    assert runtime.output_model is BodyReportOutput
    assert runtime.llm_bindings == {"analysis": "multimodal"}
