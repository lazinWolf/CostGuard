"""Structured events: one JSON line per thing that happened.

Observability hooks in here. The default sink prints a JSON line to stdout, where
`docker compose logs` or any log shipper can read it. Another backend (OpenTelemetry,
Prometheus, Langfuse) is one more function passed to `add_sink`; callers do not change.
"""

import json
import sys
import time
from typing import Callable

Sink = Callable[[dict], None]


def _stdout(event: dict) -> None:
    print(json.dumps(event, default=str), file=sys.stdout, flush=True)


_sinks: list[Sink] = [_stdout]


def add_sink(sink: Sink) -> None:
    _sinks.append(sink)


def clear_sinks() -> None:
    """Remove every sink, including the default one (tests and embedding applications)."""
    _sinks.clear()


def emit(kind: str, **fields) -> dict:
    """Record that `kind` happened. A failing sink never breaks the work that emitted."""
    event = {"event": kind, "at": round(time.time(), 3), **fields}
    for sink in list(_sinks):
        try:
            sink(event)
        except Exception:
            pass
    return event
