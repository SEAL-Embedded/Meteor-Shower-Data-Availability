"""Event catalogue adapters.

Events are other people's observations. We place them on our timeline, attribute them, and link back
to the original record; we never present them as our own measurements.

The two network adapters below are deliberately inert. Each is gated on confirming how that
catalogue may be accessed and reused, and a placeholder that quietly invents data would be worse
than one that refuses. Until a gate is cleared, use :class:`JsonEventAdapter` or the CSV event
adapter with an export you obtained yourself.

The American Meteor Society adapter is no longer among them; it lives in :mod:`.ams` and talks to
that society's documented API.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ..models import Event, EventKind, EventLocation, SourceKind, SourceStatus, parse_time
from .base import Adapter, EventResult, register


@register("json_events")
class JsonEventAdapter(Adapter):
    """Read events from a JSON file already in the published event shape.

    Accepts either a bare array of event objects or an object with an ``events`` key. Field names
    match ``docs/data-format.md``; ``id``, ``kind`` and ``time`` are required.
    """

    kind = SourceKind.EVENT

    def fetch(self) -> EventResult:
        path = self.config.resolve(self.required_option("path"))
        if not path.is_file():
            return EventResult(source=self.describe(SourceStatus.ERROR, f"no such file: {path}"))

        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as exc:
            return EventResult(
                source=self.describe(SourceStatus.ERROR, f"{path.name} is not readable JSON: {exc}")
            )

        entries = payload.get("events", []) if isinstance(payload, dict) else payload
        if not isinstance(entries, list):
            return EventResult(
                source=self.describe(
                    SourceStatus.ERROR,
                    f"{path.name} should hold a list of events, or an object with an 'events' list",
                )
            )

        events: list[Event] = []
        problems: list[str] = []
        for position, entry in enumerate(entries):
            try:
                events.append(self._event(entry))
            except (KeyError, TypeError, ValueError) as exc:
                problems.append(f"entry {position}: {exc}")

        if problems and not events:
            return EventResult(
                source=self.describe(
                    SourceStatus.ERROR, f"no usable events in {path.name}: {problems[0]}"
                )
            )
        status = SourceStatus.STALE if problems else SourceStatus.OK
        detail = (
            f"{len(problems)} entr(ies) skipped: " + "; ".join(problems[:5]) if problems else None
        )
        return EventResult(source=self.describe(status, detail), events=events)

    def _event(self, entry: dict[str, Any]) -> Event:
        location = entry.get("location") or None
        return Event(
            id=entry["id"],
            source_id=self.source_config.id,
            source_ref=entry.get("source_ref"),
            kind=EventKind(entry.get("kind", "other")),
            time=parse_time(entry["time"]),
            end_time=parse_time(entry["end_time"]) if entry.get("end_time") else None,
            time_uncertainty_s=entry.get("time_uncertainty_s"),
            label=entry.get("label"),
            location=(
                EventLocation(
                    label=location.get("label"),
                    latitude=location.get("latitude"),
                    longitude=location.get("longitude"),
                )
                if location
                else None
            ),
            magnitude=entry.get("magnitude"),
            duration_s=entry.get("duration_s"),
            url=entry.get("url"),
        )


class _GatedCatalogue(Adapter):
    """A network catalogue that is not wired up yet, and says exactly why."""

    kind = SourceKind.EVENT
    gate = "access terms have not been confirmed"

    def fetch(self) -> EventResult:
        return EventResult(source=self.describe(SourceStatus.DISABLED, self.gate))


@register("nasa_fireballs")
class NasaFireballAdapter(_GatedCatalogue):
    """NASA All Sky Fireball Network event tables."""

    gate = (
        "not enabled: the published event table format and update cadence must be confirmed before "
        "this source is switched on."
    )


@register("launch_schedule")
class LaunchScheduleAdapter(_GatedCatalogue):
    """Orbital launch schedule."""

    gate = (
        "not enabled: a launch data provider has not been chosen, and its rate limits and "
        "attribution requirements must be confirmed before this source is switched on."
    )
