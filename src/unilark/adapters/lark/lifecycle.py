"""Narrow lifecycle compatibility shim for the pinned Channel SDK 1.4.0."""

from __future__ import annotations

import threading
from collections.abc import Callable
from typing import Any


def serialize_shutdown(sdk: Any) -> None:
    """SDK stop and its start-thread failure cleanup both sweep the same loop.

    A normal WebSocket stop causes run_until_complete to raise on the transport
    thread, which enters _cleanup_failed_start while stop is still draining.
    Serializing both paths prevents each sweep cancelling the other sweep.
    Keep the SDK implementation, with one per-instance lock around its teardown.
    """
    guard = threading.RLock()
    for name in ("stop", "_cleanup_failed_start"):
        method = getattr(sdk, name)

        def serialized(*args: Any, _method: Callable[..., Any] = method, **kwargs: Any) -> Any:
            with guard:
                return _method(*args, **kwargs)

        setattr(sdk, name, serialized)
