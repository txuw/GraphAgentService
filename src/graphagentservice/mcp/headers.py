from __future__ import annotations

from collections.abc import Mapping

from graphagentservice.common.trace import TRACE_ID_HEADER


class MCPHeaderForwarder:
    def build_forward_headers(
        self,
        *,
        request_headers: dict[str, str],
        connection_headers: dict[str, str],
    ) -> dict[str, str]:
        merged_headers = {
            str(key): str(value)
            for key, value in connection_headers.items()
            if key and value is not None
        }

        merged_headers = self._replace_header(
            headers=merged_headers,
            source_headers=connection_headers,
            header_name="authorization",
            canonical_name="Authorization",
        )
        merged_headers = self._replace_header(
            headers=merged_headers,
            source_headers=connection_headers,
            header_name=TRACE_ID_HEADER,
            canonical_name=TRACE_ID_HEADER,
        )
        merged_headers = self._replace_header(
            headers=merged_headers,
            source_headers=request_headers,
            header_name="authorization",
            canonical_name="Authorization",
        )
        merged_headers = self._replace_header(
            headers=merged_headers,
            source_headers=request_headers,
            header_name=TRACE_ID_HEADER,
            canonical_name=TRACE_ID_HEADER,
        )

        return merged_headers

    def _replace_header(
        self,
        *,
        headers: dict[str, str],
        source_headers: Mapping[str, str],
        header_name: str,
        canonical_name: str,
    ) -> dict[str, str]:
        header_value = self._find_header(
            headers=source_headers,
            header_name=header_name,
        )
        if header_value is None:
            return headers

        updated_headers = {
            key: value
            for key, value in headers.items()
            if key.lower() != header_name.lower()
        }
        updated_headers[canonical_name] = header_value
        return updated_headers

    @staticmethod
    def _find_header(
        *,
        headers: Mapping[str, str],
        header_name: str,
    ) -> str | None:
        target_name = header_name.lower()
        for key, value in headers.items():
            if key.lower() == target_name and value is not None:
                return str(value)
        return None
