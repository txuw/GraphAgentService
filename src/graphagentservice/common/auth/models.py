from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True, slots=True)
class AuthenticatedUser:
    user_id: str | None
    subject: str | None
    claims: Mapping[str, Any] = field(default_factory=dict)
    is_authenticated: bool = False

    @classmethod
    def anonymous(cls) -> "AuthenticatedUser":
        return cls(
            user_id=None,
            subject=None,
            claims={},
            is_authenticated=False,
        )

    @classmethod
    def from_claims(cls, claims: Mapping[str, Any]) -> "AuthenticatedUser":
        subject = str(claims.get("sub", "")).strip() or None
        return cls(
            user_id=subject,
            subject=subject,
            claims=dict(claims),
            is_authenticated=bool(subject),
        )
