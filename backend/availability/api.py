"""HTTP API.

Serves exactly what the published snapshot contains, plus live status. A client that falls back from
this API to the static files sees the same structures with the same field names.
"""

from __future__ import annotations

import threading
from datetime import timedelta

from flask import Blueprint, jsonify, request

from .config import Config
from .core.snapshot import (
    index_payload,
    range_payload,
    status_payload,
    year_payload,
)
from .models import SCHEMA_VERSION, EventKind, Span, now, parse_time
from .store import Store

DEFAULT_REFRESH_S = 60.0


class StoreCache:
    """Holds a built store, rebuilding it when it goes stale.

    Ingest walks directories and reads files; doing that per request would make the API's cost
    scale with traffic rather than with data.
    """

    def __init__(self, config: Config, refresh_s: float = DEFAULT_REFRESH_S) -> None:
        self._config = config
        self._refresh = timedelta(seconds=refresh_s)
        self._lock = threading.Lock()
        self._store: Store | None = None

    def get(self) -> Store:
        with self._lock:
            if self._store is None or now() - self._store.generated_at >= self._refresh:
                self._store = Store.build(self._config)
            return self._store

    def invalidate(self) -> None:
        with self._lock:
            self._store = None


def create_api(cache: StoreCache) -> Blueprint:
    api = Blueprint("api", __name__)

    @api.get("/health")
    def health():
        return jsonify({"status": "ok", "schema_version": SCHEMA_VERSION})

    @api.get("/index")
    def index():
        return jsonify(index_payload(cache.get()))

    @api.get("/years/<int:year>")
    def year(year: int):
        store = cache.get()
        if year not in store.years:
            return _error("not_found", f"No data for year {year}", 404)
        return jsonify(year_payload(store, year))

    @api.get("/coverage")
    def coverage():
        store = cache.get()
        window, failure = _window_from_request(store, store.range)
        if failure is not None:
            return failure
        payload = range_payload(store, window)
        return jsonify(
            {
                "schema_version": payload["schema_version"],
                "generated_at": payload["generated_at"],
                "range": payload["range"],
                "coverage": payload["coverage"],
                "segments": payload["segments"],
            }
        )

    @api.get("/events")
    def events():
        store = cache.get()
        window, failure = _window_from_request(store, store.full_range)
        if failure is not None:
            return failure

        payload = range_payload(store, window)
        selected, failure = _selected_events(store, payload["events"])
        if failure is not None:
            return failure

        wanted = {event["id"] for event in selected}
        return jsonify(
            {
                "schema_version": payload["schema_version"],
                "generated_at": payload["generated_at"],
                "range": payload["range"],
                "events": selected,
                "event_coverage": [
                    record
                    for record in payload["event_coverage"]
                    if record["event_id"] in wanted
                ],
            }
        )

    @api.get("/status")
    def status():
        return jsonify(status_payload(cache.get()))

    return api


def _selected_events(store: Store, events: list[dict]):
    """Apply the optional ``source`` and ``kind`` filters, both comma-separated.

    An unrecognised value is refused rather than quietly returning nothing: a caller who
    misspells a source id should be told so, not handed an empty list that looks like a
    quiet period.
    """
    selected = events
    for parameter, field, known in (
        ("source", "source_id", {source.id for source in store.sources}),
        ("kind", "kind", {kind.value for kind in EventKind}),
    ):
        raw = request.args.get(parameter)
        if raw is None:
            continue
        wanted = {value.strip() for value in raw.split(",") if value.strip()}
        unknown = wanted - known
        if unknown:
            return None, _error(
                "bad_request",
                f"Unknown {parameter}: {', '.join(sorted(unknown))}. "
                f"Available: {', '.join(sorted(known))}",
                400,
            )
        selected = [event for event in selected if event.get(field) in wanted]
    return selected, None


def _window_from_request(store: Store, default: Span | None = None):
    """Read ``start``/``end`` query parameters, falling back to *default*."""
    raw_start = request.args.get("start")
    raw_end = request.args.get("end")

    if raw_start is None and raw_end is None:
        if default is None:
            return None, _error("no_data", "Nothing has been ingested yet", 404)
        return default, None

    try:
        start = parse_time(raw_start) if raw_start else (default.start if default else None)
        end = parse_time(raw_end) if raw_end else (default.end if default else None)
    except ValueError as exc:
        return None, _error("bad_request", f"Unreadable timestamp: {exc}", 400)

    if start is None or end is None:
        return None, _error("bad_request", "Both start and end are required here", 400)
    if end < start:
        return None, _error("bad_request", "end must not precede start", 400)
    return Span(start, end), None


def _error(code: str, message: str, status: int):
    response = jsonify({"error": code, "message": message})
    response.status_code = status
    return response
