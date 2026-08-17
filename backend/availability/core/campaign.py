"""Export the record in the dashboard's dataset shape.

The dashboard in ``Data Availability Dashboard.dc.html`` reads a dataset of its own design, with
epoch-millisecond timestamps and its own controlled vocabularies. Rather than bend either side, this
translates ours into theirs at publish time, so the dashboard's own comment -- *replace
buildDataset() with a fetch of data/2024-campaign.json* -- becomes true.

Two rules govern the translation:

**Only fields we actually hold are emitted.** The dashboard applies its own defaults to whatever is
missing, so an absent field reads as "not determined" rather than as a value we invented.

**A human check is still a check.** These records come from a season sheet an operator kept: hours
watched, and where data was lost. That is a validation. Reporting it as ``unchecked`` because no
scanner has run discarded the sheet's judgement, and since the dashboard draws unchecked coverage as
a hollow outline, it made a fully characterised season look like one nobody had ever examined.
``checkMethod`` says how the check was made rather than pretending it was automated.
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Any

from ..models import CoverageInterval, Event, EventKind, Instrument, Quality, Span

if TYPE_CHECKING:  # pragma: no cover
    from ..config import CampaignConfig
    from ..store import Store

#: Our quality, in the dashboard's terms: ``validation``, ``status``, ``lossSeverity``.
#:
#: These records were checked. An operator watched the instruments through the season and wrote
#: down where data was lost; that is a validation, just a human one rather than a scanner's.
#: Reporting it as ``unchecked`` threw away the sheet's judgement -- and, because the dashboard
#: draws unchecked coverage as a hollow outline, made a fully characterised season render as if
#: nothing had been examined at all.
_QUALITY = {
    Quality.GOOD: ("valid", "ok", "none"),
    Quality.DEGRADED: ("valid", "partial", "minor"),
    Quality.LOST: ("invalid", "corrupt", "major"),
}

#: How the check was made. Not one of the scanner's methods, because no scanner ran.
CHECK_METHOD = "operator_log"

_EVENT_CLASS = {
    EventKind.FIREBALL: "fireball",
    EventKind.METEOR_SHOWER: "meteor",
    EventKind.ROCKET_LAUNCH: "launch",
    EventKind.OTHER: "meteor",
}

_MODALITY = {
    "vlf": "radio",
    "magnetometer": "field",
    "sky_camera": "optical",
    "other": "radio",
}


def milliseconds(moment: datetime) -> int:
    return int(moment.timestamp() * 1000)


def campaign_payload(store: "Store", config: "CampaignConfig") -> dict[str, Any]:
    """Build the dashboard's dataset from the record."""
    # The campaign is the period the instruments were characterised over, not the period events
    # are known for. A year of catalogue events would otherwise stretch a 45-night observing run
    # across the whole calendar and make the dashboard's ribbon meaningless.
    payload: dict[str, Any] = {
        "meta": _meta(store, config),
        "site": _site(store, config),
        "campaign": _campaign(store.range),
        "instruments": [_instrument(i, config) for i in store.instruments],
        "coverage": [
            record
            for record in (_coverage(c, config) for c in store.coverage)
            if record is not None
        ],
        "events": [_event(e, config) for e in store.events],
    }
    jumps = _jumps(store)
    if jumps:
        payload["jumps"] = jumps
    return payload


def _meta(store: "Store", config: "CampaignConfig") -> dict[str, Any]:
    sources = ", ".join(
        f"{source.name} ({source.status.value})" for source in store.sources
    )
    return {
        "datasetVersion": config.dataset_version,
        "scanTimestamp": store.generated_at.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "scannerVersion": config.scanner_version,
        "provenance": "measured",
        "sourceNote": sources or "no sources configured",
    }


def _site(store: "Store", config: "CampaignConfig") -> dict[str, Any] | None:
    """The site the instruments actually declare, rather than an assumed one."""
    for instrument in store.instruments:
        if instrument.site and instrument.site.latitude is not None:
            return {
                "label": instrument.site.name,
                "lat": instrument.site.latitude,
                "lon": instrument.site.longitude,
                "assumed": False,
                **({"altM": config.site_altitude_m} if config.site_altitude_m is not None else {}),
            }
    return None


def _campaign(window: Span | None) -> dict[str, int] | None:
    if window is None:
        return None
    return {"start": milliseconds(window.start), "end": milliseconds(window.end)}


def _instrument(instrument: Instrument, config: "CampaignConfig") -> dict[str, Any]:
    return {
        "id": config.export_id(instrument.id),
        "name": instrument.name,
        "short": instrument.name.split("—")[0].strip(),
        "modality": _MODALITY.get(instrument.kind.value, "radio"),
        "active": instrument.active,
    }


def _coverage(record: CoverageInterval, config: "CampaignConfig") -> dict[str, Any] | None:
    validation, status, loss = _QUALITY[record.quality]
    note = record.note or ""
    payload: dict[str, Any] = {
        "recordKind": "coverage",
        "id": f"{record.instrument_id}-{milliseconds(record.start)}",
        "instrumentId": config.export_id(record.instrument_id),
        "start": milliseconds(record.start),
        "end": milliseconds(record.end),
        "validation": validation,
        "checkMethod": CHECK_METHOD,
        "status": status,
        "lossSeverity": loss,
        "timeScale": "utc",
        "provenance": config.provenance_for(record.source_id),
    }
    if note:
        payload["label"] = note
        if "recover" in note.lower():
            payload["lossRecoverable"] = True
    return payload


_HOUR_MS = 3_600_000
_DAY_MS = 86_400_000

#: How much room a jump leaves around the thing it points at. A window exactly the size of its
#: subject shows it with no context either side, which is the opposite of what a jump is for.
_JUMP_PAD = 0.35


def _jumps(store: "Store") -> list[dict[str, Any]]:
    """Notable windows in the record, for the dashboard's jump bar.

    These were literal timestamps typed against a sample dataset. By the time the measured record
    replaced that sample, one of them -- the button labelled *Gap* -- pointed into the middle of a
    four-day unbroken run, so a control asserting we were down sat on the most continuous stretch
    of the season. That is the precise confusion this project exists to prevent, so the windows are
    computed here instead, once, at publish time: the numbers a reader might quote are then
    reproducible from the dataset rather than recomputed differently by every client.

    An entry is emitted only when the record actually contains one. A campaign with no gap
    publishes no gap jump, and the dashboard therefore cannot offer a button promising something
    that is not there.
    """
    window = store.range
    if window is None:
        return []

    start_ms, end_ms = milliseconds(window.start), milliseconds(window.end)
    jumps: list[dict[str, Any]] = [
        {
            "id": "campaign",
            "label": "Whole campaign",
            "start": start_ms,
            "end": end_ms,
            "detail": _spell(end_ms - start_ms),
        }
    ]

    def clip(begin: int, finish: int) -> tuple[int, int]:
        return max(start_ms, begin), min(end_ms, finish)

    def framed(begin: int, finish: int) -> tuple[int, int]:
        pad = max(int((finish - begin) * _JUMP_PAD), 5 * 60_000)
        return clip(begin - pad, finish + pad)

    # Only events inside the characterised period. The record deliberately keeps events outside it
    # -- those are the ones carrying an ``unknown`` verdict -- but a jump to a period we never
    # examined would frame an empty window and invite it to be read as downtime.
    moments = sorted(
        moment
        for moment in (milliseconds(event.time) for event in store.events)
        if start_ms <= moment <= end_ms
    )
    night = _densest(moments, 14 * _HOUR_MS)
    if night is not None:
        at, count = night
        begin, finish = clip(at, at + 14 * _HOUR_MS)
        jumps.append({
            "id": "busiest-night",
            "label": "Busiest night",
            "start": begin,
            "end": finish,
            "detail": f"{count} events in 14 h",
        })
    hour = _densest(moments, _HOUR_MS)
    if hour is not None:
        at, count = hour
        begin, finish = framed(at, at + _HOUR_MS)
        jumps.append({
            "id": "dense-hour",
            "label": "Dense hour",
            "start": begin,
            "end": finish,
            "detail": f"{count} events within an hour",
        })
        focus = next((moment for moment in moments if moment >= at), None)
        if focus is not None:
            begin, finish = clip(focus - 5 * 60_000, focus + 5 * 60_000)
            jumps.append({
                "id": "closest-look",
                "label": "Closest look",
                "start": begin,
                "end": finish,
                "detail": "a single event, ±5 min",
            })

    coverage = sorted(store.coverage, key=lambda record: record.start)
    if coverage:
        shortest = min(coverage, key=lambda record: record.end - record.start)
        length = milliseconds(shortest.end) - milliseconds(shortest.start)
        begin, finish = framed(milliseconds(shortest.start), milliseconds(shortest.end))
        jumps.append({
            "id": "shortest-run",
            "label": "Shortest run",
            "start": begin,
            "end": finish,
            "detail": f"{_spell(length)} of recording",
        })

        by_day: dict[str, list[CoverageInterval]] = {}
        for record in coverage:
            by_day.setdefault(record.start.strftime("%Y-%m-%d"), []).append(record)
        day, busiest = max(by_day.items(), key=lambda item: (len(item[1]), item[0]))
        if len(busiest) > 1:
            jumps.append({
                "id": "most-fragmented",
                "label": "Most fragmented",
                "start": milliseconds(min(record.start for record in busiest)),
                "end": milliseconds(max(record.end for record in busiest)),
                "detail": f"{len(busiest)} separate runs on {day}",
            })

        gap = _largest_gap(coverage)
        if gap is not None:
            opened, closed = gap
            begin, finish = framed(opened, closed)
            jumps.append({
                "id": "largest-gap",
                "label": "Largest gap",
                "start": begin,
                "end": finish,
                "detail": f"{_spell(closed - opened)} with nothing recording",
            })

    return jumps


def _densest(moments: list[int], width: int) -> tuple[int, int] | None:
    """Start of the ``width``-long window holding the most of ``moments``, and how many."""
    if not moments:
        return None
    best_start, best_count, left = moments[0], 0, 0
    for right, moment in enumerate(moments):
        while moment - moments[left] > width:
            left += 1
        if right - left + 1 > best_count:
            best_count, best_start = right - left + 1, moments[left]
    return best_start, best_count


def _largest_gap(coverage: list[CoverageInterval]) -> tuple[int, int] | None:
    """The longest stretch inside the characterised period with no instrument recording.

    Computed over the union of every instrument's coverage: a gap for one instrument while another
    was running is not a gap in the record.
    """
    merged: list[list[int]] = []
    for record in coverage:
        begin, finish = milliseconds(record.start), milliseconds(record.end)
        if merged and begin <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], finish)
        else:
            merged.append([begin, finish])
    widest: tuple[int, int] | None = None
    for earlier, later in zip(merged, merged[1:]):
        if widest is None or later[0] - earlier[1] > widest[1] - widest[0]:
            widest = (earlier[1], later[0])
    return widest


def _spell(length: int) -> str:
    """A duration in the largest unit that still says something useful."""
    if length < 90 * 60_000:
        return f"{length / 60_000:.0f} min"
    if length < 2 * _DAY_MS:
        hours = length / _HOUR_MS
        return f"{hours:.1f} h" if hours < 10 else f"{hours:.0f} h"
    return f"{length / _DAY_MS:.0f} days"


def _event(event: Event, config: "CampaignConfig") -> dict[str, Any]:
    payload: dict[str, Any] = {
        "recordKind": "event",
        "id": event.id,
        "eventClass": _EVENT_CLASS.get(event.kind, "meteor"),
        "eventSource": config.event_source_for(event.source_id),
        "start": milliseconds(event.time),
        "provenance": config.provenance_for(event.source_id),
    }
    if event.end_time is not None:
        payload["eventEnd"] = milliseconds(event.end_time)
    if event.time_uncertainty_s is not None:
        # The source states times to the minute; that is a reported precision, not a fitted one.
        payload["uncertaintyBasis"] = "reported_precision"
        payload["uncertaintySec"] = event.time_uncertainty_s
    if event.duration_s is not None:
        payload["eventDurationSec"] = event.duration_s
    if event.magnitude is not None:
        payload["magnitudeValue"] = event.magnitude
        payload["magnitudeBasis"] = "witness_estimate"
    if event.witness_count is not None:
        payload["witnessCount"] = event.witness_count
    if event.location and event.location.label:
        payload["eventLocation"] = event.location.label
    if event.source_ref:
        payload["eventRefId"] = event.source_ref
    if event.url:
        payload["referenceUrl"] = event.url
    if event.label:
        payload["label"] = event.label
    return payload
