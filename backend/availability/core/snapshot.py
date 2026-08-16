"""Serialise the store into the published contract.

Payloads are produced here and nowhere else, so the static snapshot and the live API return byte-for
-byte equivalent structures. See ``docs/data-format.md``; change one and you must change the other.

Snapshots are written with indentation because they are committed to the repository. A readable diff
is what lets a reviewer see that last night's run changed one coverage window rather than rewriting
the year.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ..models import SCHEMA_VERSION, Span, iso

if TYPE_CHECKING:  # pragma: no cover - import cycle only matters to type checkers
    from ..store import Store


def index_payload(store: "Store") -> dict[str, Any]:
    window = store.range
    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": iso(store.generated_at),
        "range": window.to_dict() if window else None,
        "years": store.years,
        "instruments": [instrument.to_dict() for instrument in store.instruments],
        "sources": [source.to_dict() for source in store.sources],
    }


def year_payload(store: "Store", year: int) -> dict[str, Any]:
    from ..store import year_window

    window = year_window(year)
    events = store.events_in(window)
    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": iso(store.generated_at),
        "year": year,
        "coverage": [record.to_dict() for record in store.coverage_in(window)],
        "segments": [segment.to_dict() for segment in store.segments_in(window)],
        "events": [event.to_dict() for event in events],
        "event_coverage": [record.to_dict() for record in store.coverage_for(events)],
    }


def range_payload(store: "Store", window: Span) -> dict[str, Any]:
    events = store.events_in(window)
    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": iso(store.generated_at),
        "range": window.to_dict(),
        "coverage": [record.to_dict() for record in store.coverage_in(window)],
        "segments": [segment.to_dict() for segment in store.segments_in(window)],
        "events": [event.to_dict() for event in events],
        "event_coverage": [record.to_dict() for record in store.coverage_for(events)],
    }


def status_payload(store: "Store", moment: datetime | None = None) -> dict[str, Any]:
    status = store.status(moment)
    return {
        "as_of": iso(status["as_of"]),
        "recording": [
            {
                "instrument_id": entry["instrument_id"],
                "quality": entry["quality"],
                "since": iso(entry["since"]),
            }
            for entry in status["recording"]
        ],
        "degree": status["degree"],
    }


def write_campaign(store: "Store", config) -> Path:
    """Write the dashboard's dataset, in its shape, where it expects to find it."""
    from .campaign import campaign_payload

    target = config.resolve(config.campaign.path)
    target.parent.mkdir(parents=True, exist_ok=True)
    return _write(target, campaign_payload(store, config.campaign))


def write_snapshot(store: "Store", output_dir: Path) -> list[Path]:
    """Write ``index.json`` and one file per year. Returns the paths written."""
    output_dir.mkdir(parents=True, exist_ok=True)
    written = [_write(output_dir / "index.json", index_payload(store))]
    for year in store.years:
        written.append(_write(output_dir / f"{year}.json", year_payload(store, year)))
    return written


def _write(path: Path, payload: dict[str, Any]) -> Path:
    text = json.dumps(payload, indent=2, ensure_ascii=False)
    path.write_text(text + "\n", encoding="utf-8")
    return path
