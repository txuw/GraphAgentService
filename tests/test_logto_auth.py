from __future__ import annotations

from fastapi import Depends, Request
from fastapi.testclient import TestClient
from jwt.exceptions import InvalidAudienceError, InvalidIssuerError

from overmindagent.api.dependencies import get_current_user, require_current_user
from overmindagent.common.auth import AuthenticatedUser, LogtoAuthenticator
from overmindagent.main import create_app

ES384_TEST_TOKEN = (
    "eyJhbGciOiJFUzM4NCIsInR5cCI6ImF0K2p3dCIsImtpZCI6InRlc3Qta2lkIn0."
    "eyJzdWIiOiJ1c2VyLWFiYyJ9."
    "c2lnbmF0dXJl"
)


class FakeSigningKey:
    def __init__(self, key: str = "public-key", algorithm_name: str = "ES384") -> None:
        self.key = key
        self.algorithm_name = algorithm_name


class FakeJWKClient:
    def get_signing_key_from_jwt(self, token: str) -> FakeSigningKey:
        return FakeSigningKey()


def test_logto_authenticator_returns_anonymous_user_when_disabled() -> None:
    authenticator = LogtoAuthenticator({"enabled": False})
    app = create_app()
    app.state.logto_authenticator = authenticator

    @app.get("/api/test-auth-disabled", dependencies=[Depends(require_current_user)])
    async def disabled_auth_endpoint(
        request: Request,
        current_user: AuthenticatedUser = Depends(get_current_user),
    ) -> dict[str, object]:
        return {
            "user_id": current_user.user_id,
            "is_authenticated": current_user.is_authenticated,
            "state_user_id": request.state.current_user.user_id,
        }

    with TestClient(app) as client:
        response = client.get("/api/test-auth-disabled")

    assert response.status_code == 200
    assert response.json() == {
        "user_id": None,
        "is_authenticated": False,
        "state_user_id": None,
    }


def test_logto_authenticator_rejects_missing_authorization_header() -> None:
    app = create_app()

    with TestClient(app) as client:
        response = client.get("/api/graphs")

    assert response.status_code == 401
    assert response.json() == {"detail": "Authorization header is missing."}


def test_logto_authenticator_accepts_valid_token_and_sets_current_user() -> None:
    def fake_decoder(
        token: str,
        key: str,
        *,
        algorithms: list[str],
        issuer: str,
        audience: str,
    ) -> dict[str, object]:
        assert token == ES384_TEST_TOKEN
        assert key == "public-key"
        assert algorithms == ["ES384"]
        assert issuer == "https://login.txuw.top/oidc"
        assert audience == "https://api.txuw.top"
        return {"sub": "user-abc", "scope": "api:read"}

    authenticator = LogtoAuthenticator(
        {
            "enabled": True,
            "issuer_uri": "https://login.txuw.top/oidc",
            "audience": "https://api.txuw.top",
            "jwk_set_uri": "https://login.txuw.top/oidc/jwks",
        },
        jwk_client=FakeJWKClient(),
        jwt_decoder=fake_decoder,
    )
    app = create_app()
    app.state.logto_authenticator = authenticator

    @app.get("/api/test-auth-success", dependencies=[Depends(require_current_user)])
    async def auth_success_endpoint(
        request: Request,
        current_user: AuthenticatedUser = Depends(get_current_user),
    ) -> dict[str, object]:
        return {
            "user_id": current_user.user_id,
            "subject": current_user.subject,
            "state_user_id": request.state.current_user.user_id,
        }

    with TestClient(app) as client:
        response = client.get(
            "/api/test-auth-success",
            headers={"Authorization": f"Bearer {ES384_TEST_TOKEN}"},
        )

    assert response.status_code == 200
    assert response.json() == {
        "user_id": "user-abc",
        "subject": "user-abc",
        "state_user_id": "user-abc",
    }


def test_logto_authenticator_rejects_invalid_issuer() -> None:
    def fake_decoder(
        token: str,
        key: str,
        *,
        algorithms: list[str],
        issuer: str,
        audience: str,
    ) -> dict[str, object]:
        raise InvalidIssuerError("bad issuer")

    authenticator = LogtoAuthenticator(
        {
            "enabled": True,
            "issuer-uri": "https://login.txuw.top/oidc",
            "audience": "https://api.txuw.top",
            "jwk-set-uri": "https://login.txuw.top/oidc/jwks",
        },
        jwk_client=FakeJWKClient(),
        jwt_decoder=fake_decoder,
    )
    app = create_app()
    app.state.logto_authenticator = authenticator

    with TestClient(app) as client:
        response = client.get(
            "/api/graphs",
            headers={"Authorization": f"Bearer {ES384_TEST_TOKEN}"},
        )

    assert response.status_code == 401
    assert response.json() == {"detail": "Invalid access token."}


def test_logto_authenticator_rejects_invalid_audience() -> None:
    def fake_decoder(
        token: str,
        key: str,
        *,
        algorithms: list[str],
        issuer: str,
        audience: str,
    ) -> dict[str, object]:
        raise InvalidAudienceError("bad audience")

    authenticator = LogtoAuthenticator(
        {
            "enabled": True,
            "issuer_uri": "https://login.txuw.top/oidc",
            "audience": "https://api.txuw.top",
            "jwk_set_uri": "https://login.txuw.top/oidc/jwks",
        },
        jwk_client=FakeJWKClient(),
        jwt_decoder=fake_decoder,
    )
    app = create_app()
    app.state.logto_authenticator = authenticator

    with TestClient(app) as client:
        response = client.get(
            "/api/graphs",
            headers={"Authorization": f"Bearer {ES384_TEST_TOKEN}"},
        )

    assert response.status_code == 401
    assert response.json() == {"detail": "Invalid access token."}
