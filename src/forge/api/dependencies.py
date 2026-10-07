"""Access to the application's wired dependencies from request handlers."""

from typing import cast

from fastapi import Request

from forge.domain.repositories import Clock, UnitOfWorkFactory


def uow_factory(request: Request) -> UnitOfWorkFactory:
    return cast(UnitOfWorkFactory, request.app.state.uow_factory)


def clock(request: Request) -> Clock:
    return cast(Clock, request.app.state.clock)
