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
    return {
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
