"""Access to the application's wired dependencies from request handlers."""

import hmac
from collections.abc import Mapping
from typing import cast

from fastapi import HTTPException, Request, status

from forge.domain.repositories import Clock, UnitOfWorkFactory


def uow_factory(request: Request) -> UnitOfWorkFactory:
    return cast(UnitOfWorkFactory, request.app.state.uow_factory)


def clock(request: Request) -> Clock:
    return cast(Clock, request.app.state.clock)


def operator(request: Request) -> str:
    """The authenticated human behind a governance action (ADR 0010), else 401.

    Agents hold no operator token, so no agent can approve, write policy, or promote.
    """
    tokens = cast(Mapping[str, str], request.app.state.operator_tokens)
    scheme, _, presented = request.headers.get("authorization", "").partition(" ")
    if scheme.lower() == "bearer" and presented:
        for token, name in tokens.items():
            if hmac.compare_digest(token.encode(), presented.encode()):
                return name
    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="an operator token is required",
        headers={"WWW-Authenticate": "Bearer"},
    )
