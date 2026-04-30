"""
Realtime value registry for read-only TUI panels.
"""

from __future__ import annotations

import inspect
from dataclasses import dataclass
from typing import Awaitable, Callable, TypeVar

from api.command_handlers import CliHandlerContext

RealtimeResult = str | int | float | bool | None
RealtimeFn = Callable[[CliHandlerContext], RealtimeResult | Awaitable[RealtimeResult]]
RealtimeNoArgFn = Callable[[], RealtimeResult | Awaitable[RealtimeResult]]
RegisteredRealtimeFn = Callable[
    [CliHandlerContext], RealtimeResult | Awaitable[RealtimeResult]
]


@dataclass(frozen=True)
class RealtimeVariableSpec:
    name: str
    getter: RegisteredRealtimeFn
    refresh_seconds: float
    order: int


REALTIME_VARIABLES: dict[str, RealtimeVariableSpec] = {}


def _ensure_ctx_getter(
    fn: RealtimeFn | RealtimeNoArgFn,
) -> RegisteredRealtimeFn:
    sig = inspect.signature(fn)
    if len(sig.parameters) == 0:

        def noarg_wrapper(
            _ctx: CliHandlerContext, _fn: RealtimeNoArgFn = fn
        ) -> RealtimeResult | Awaitable[RealtimeResult]:
            return _fn()

        return noarg_wrapper

    return fn


FRealtime = TypeVar("FRealtime", bound=RealtimeFn | RealtimeNoArgFn)


def register_realtime_variable(
    name: str | None = None,
    *,
    refresh_seconds: float = 1.0,
    order: int = 100,
) -> Callable[[FRealtime], FRealtime]:
    """
    Decorator that registers a value getter for the realtime TUI.

    The decorated function can be sync or async, and may accept either:
    - ``()`` (no args), or
    - ``(ctx: CliHandlerContext)``.
    """

    def decorator(fn: FRealtime) -> FRealtime:
        key = (name or fn.__name__).strip()
        if not key:
            raise ValueError("Realtime variable name cannot be empty.")
        REALTIME_VARIABLES[key] = RealtimeVariableSpec(
            name=key,
            getter=_ensure_ctx_getter(fn),
            refresh_seconds=max(0.1, float(refresh_seconds)),
            order=order,
        )
        return fn

    return decorator
