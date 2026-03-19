from __future__ import annotations

from collections.abc import Mapping


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

        connection_authorization = self._find_header(
            headers=connection_headers,
            header_name="authorization",
        )
        if connection_authorization is not None:
            merged_headers = {
                key: value
                for key, value in merged_headers.items()
                if key.lower() != "authorization"
            }
            merged_headers["Authorization"] = connection_authorization

        request_authorization = self._find_header(
            headers=request_headers,
            header_name="authorization",
        )
        if request_authorization is not None:
            merged_headers = {
                key: value
                for key, value in merged_headers.items()
                if key.lower() != "authorization"
            }
            merged_headers["Authorization"] = request_authorization

        return merged_headers

    @staticmethod
    def _find_header(
        *,
        headers: Mapping[str, str],
        header_name: str,
    ) -> str | None:
        for key, value in headers.items():
            if key.lower() == header_name and value is not None:
                return str(value)
        return None

