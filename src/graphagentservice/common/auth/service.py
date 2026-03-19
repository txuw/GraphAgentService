from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any

import jwt
from fastapi import Request
from jwt import PyJWKClient
from jwt.exceptions import ExpiredSignatureError, InvalidAudienceError, InvalidIssuerError, InvalidTokenError, PyJWKClientError

from .errors import AuthenticationError
from .models import AuthenticatedUser


class LogtoAuthenticator:
    def __init__(
        self,
        settings: Mapping[str, Any] | Any | None = None,
        *,
        jwk_client: Any | None = None,
        jwt_decoder: Callable[..., dict[str, Any]] | None = None,
    ) -> None:
        settings = settings or {}
        self._enabled = bool(self._read_setting(settings, "enabled", default=False))
        self._issuer_uri = self._read_setting(
            settings,
            "issuer_uri",
            "issuer-uri",
            default="",
        ).strip()
        self._audience = self._read_setting(
            settings,
            "audience",
            default="",
        ).strip()
        self._jwk_set_uri = self._read_setting(
            settings,
            "jwk_set_uri",
            "jwk-set-uri",
            default="",
        ).strip()
        self._jwt_decoder = jwt_decoder or jwt.decode
        self._jwk_client = jwk_client or (
            PyJWKClient(self._jwk_set_uri) if self._enabled else None
        )
        self._validate_configuration()

    @property
    def enabled(self) -> bool:
        return self._enabled

    def authenticate_request(self, request: Request) -> AuthenticatedUser:
        cached_user = getattr(request.state, "current_user", None)
        if isinstance(cached_user, AuthenticatedUser):
            return cached_user

        if not self._enabled:
            anonymous_user = AuthenticatedUser.anonymous()
            request.state.current_user = anonymous_user
            return anonymous_user

        token = self.extract_bearer_token(request.headers)
        claims = self.validate_token(token)
        user = AuthenticatedUser.from_claims(claims)
        if not user.user_id:
            raise AuthenticationError("Invalid access token.")
        request.state.current_user = user
        return user

    def validate_token(self, token: str) -> dict[str, Any]:
        if not self._enabled:
            return {}
        if self._jwk_client is None:
            raise AuthenticationError("Authentication is not configured.")

        try:
            signing_key = self._jwk_client.get_signing_key_from_jwt(token)
            algorithms = self._resolve_algorithms(token, signing_key)
            return dict(
                self._jwt_decoder(
                    token,
                    signing_key.key,
                    algorithms=algorithms,
                    issuer=self._issuer_uri,
                    audience=self._audience,
                )
            )
        except ExpiredSignatureError as exc:
            raise AuthenticationError("Access token has expired.") from exc
        except (InvalidAudienceError, InvalidIssuerError, InvalidTokenError, PyJWKClientError) as exc:
            raise AuthenticationError("Invalid access token.") from exc
        except Exception as exc:
            raise AuthenticationError("Unable to validate access token.") from exc

    @staticmethod
    def extract_bearer_token(headers: Mapping[str, str]) -> str:
        authorization = headers.get("authorization") or headers.get("Authorization") or ""
        scheme, _, token = authorization.partition(" ")
        if not scheme:
            raise AuthenticationError("Authorization header is missing.")
        if scheme.lower() != "bearer" or not token.strip():
            raise AuthenticationError('Authorization header must start with "Bearer ".')
        return token.strip()

    @staticmethod
    def _resolve_algorithms(token: str, signing_key: Any) -> list[str]:
        token_header = jwt.get_unverified_header(token)
        token_algorithm = str(token_header.get("alg", "")).strip()
        key_algorithm = str(getattr(signing_key, "algorithm_name", "")).strip()

        if token_algorithm and key_algorithm and token_algorithm != key_algorithm:
            raise AuthenticationError("Invalid access token.")

        resolved_algorithm = key_algorithm or token_algorithm
        if not resolved_algorithm:
            raise AuthenticationError("Invalid access token.")
        return [resolved_algorithm]

    def _validate_configuration(self) -> None:
        if not self._enabled:
            return

        missing_fields = [
            field_name
            for field_name, field_value in (
                ("issuer_uri", self._issuer_uri),
                ("audience", self._audience),
                ("jwk_set_uri", self._jwk_set_uri),
            )
            if not field_value
        ]
        if missing_fields:
            missing = ", ".join(missing_fields)
            raise ValueError(f"Missing Logto configuration fields: {missing}")

    @staticmethod
    def _read_setting(
        settings: Mapping[str, Any] | Any,
        *names: str,
        default: Any = None,
    ) -> Any:
        for name in names:
            if isinstance(settings, Mapping) and name in settings:
                value = settings[name]
                if value is not None:
                    return value
            if hasattr(settings, "get"):
                value = settings.get(name)
                if value is not None:
                    return value
        return default
