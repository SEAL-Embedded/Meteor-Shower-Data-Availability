"""Data model for the availability record.

Every timestamp handled here is timezone-aware and in UTC. Naive datetimes are rejected at
construction rather than silently assumed to be UTC -- a silent assumption is how a coverage window
ends up eight hours from where it belongs.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Iterable

UTC = timezone.utc

SCHEMA_VERSION = "1.0"


# --------------------------------------------------------------------------------------
# time helpers
# --------------------------------------------------------------------------------------


def ensure_utc(value: datetime) -> datetime:
    """Return *value* as a UTC-aware datetime, rejecting naive input."""
    if value.tzinfo is None:
        raise ValueError(f"naive datetime not accepted: {value!r}; attach a timezone")
    return value.astimezone(UTC)


def parse_time(value: str | datetime) -> datetime:
    """Parse an ISO 8601 timestamp. A bare 'Z' suffix is accepted."""
    if isinstance(value, datetime):
        return ensure_utc(value)
    text = value.strip()
    if text.endswith(("z", "Z")):
        text = text[:-1] + "+00:00"
    parsed = datetime.fromisoformat(text)
    if parsed.tzinfo is None:
        raise ValueError(f"timestamp {value!r} has no timezone; UTC must be explicit")
    return parsed.astimezone(UTC)


def iso(value: datetime | None) -> str | None:
    """Serialise to the contract's timestamp form: second precision, 'Z' suffix."""
    if value is None:
        return None
    return ensure_utc(value).strftime("%Y-%m-%dT%H:%M:%SZ")


def now() -> datetime:
    return datetime.now(UTC)


# --------------------------------------------------------------------------------------
# enumerations
# --------------------------------------------------------------------------------------


class Quality(str, Enum):
    """How usable the data in a coverage interval is."""

    GOOD = "good"
    DEGRADED = "degraded"
    LOST = "lost"

    @property
    def rank(self) -> int:
        return _QUALITY_RANK[self]

    @classmethod
    def worst(cls, values: Iterable["Quality"]) -> "Quality":
        """The weakest quality in *values*; a chain is as good as its worst link."""
        candidates = list(values)
        if not candidates:
            raise ValueError("worst() requires at least one quality")
        return min(candidates, key=lambda q: q.rank)


_QUALITY_RANK = {Quality.LOST: 0, Quality.DEGRADED: 1, Quality.GOOD: 2}


class InstrumentKind(str, Enum):
    VLF = "vlf"
    MAGNETOMETER = "magnetometer"
    SKY_CAMERA = "sky_camera"
    OTHER = "other"


class EventKind(str, Enum):
    FIREBALL = "fireball"
    METEOR_SHOWER = "meteor_shower"
    ROCKET_LAUNCH = "rocket_launch"
    OTHER = "other"


class Verdict(str, Enum):
    """Whether we were recording when an event happened.

    ``UNKNOWN`` is not a softer ``NOT_COVERED``. It means the period has never been
    characterised, and must never be presented as downtime.
    """

    COVERED = "covered"
    PARTIAL = "partial"
    NOT_COVERED = "not_covered"
    UNKNOWN = "unknown"


class SourceKind(str, Enum):
    COVERAGE = "coverage"
    EVENT = "event"


class SourceStatus(str, Enum):
    OK = "ok"
    STALE = "stale"
    ERROR = "error"
    DISABLED = "disabled"


# --------------------------------------------------------------------------------------
# spans
# --------------------------------------------------------------------------------------


@dataclass(frozen=True, order=True)
class Span:
    """A half-open interval ``[start, end)``, except when start == end (an instant)."""

    start: datetime
    end: datetime

    def __post_init__(self) -> None:
        object.__setattr__(self, "start", ensure_utc(self.start))
        object.__setattr__(self, "end", ensure_utc(self.end))
        if self.end < self.start:
            raise ValueError(f"span ends before it starts: {self.start} .. {self.end}")

    @property
    def is_instant(self) -> bool:
        return self.start == self.end

    @property
    def duration_s(self) -> float:
        return (self.end - self.start).total_seconds()

    def contains(self, moment: datetime) -> bool:
        moment = ensure_utc(moment)
        if self.is_instant:
            return moment == self.start
        return self.start <= moment < self.end

    def overlaps(self, other: "Span") -> bool:
        """True when the two spans share time. Instants count as touching what holds them."""
        if self.is_instant and other.is_instant:
            return self.start == other.start
        if self.is_instant:
            return other.start <= self.start <= other.end
        if other.is_instant:
            return self.start <= other.start <= self.end
        return self.start < other.end and other.start < self.end

    def intersection(self, other: "Span") -> "Span | None":
        start = max(self.start, other.start)
        end = min(self.end, other.end)
        if end < start:
            return None
        if start == end and not (self.is_instant or other.is_instant):
            return None
        return Span(start, end)

    def clipped_to(self, window: "Span") -> "Span | None":
        return self.intersection(window)

    def to_dict(self) -> dict[str, Any]:
        return {"start": iso(self.start), "end": iso(self.end)}

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "Span":
        return cls(parse_time(raw["start"]), parse_time(raw["end"]))


# --------------------------------------------------------------------------------------
# entities
# --------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Site:
    id: str
    name: str
    latitude: float | None = None
    longitude: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "latitude": self.latitude,
            "longitude": self.longitude,
        }


@dataclass(frozen=True)
class Band:
    low: float
    high: float

    def to_dict(self) -> dict[str, Any]:
        return {"low": self.low, "high": self.high}


@dataclass
class Instrument:
    id: str
    name: str
    kind: InstrumentKind
    system: str
    site: Site | None = None
    band_hz: Band | None = None
    active: bool = True
    known_range: Span | None = None
    """The period over which this instrument's availability has been characterised.

    Outside it, the absence of a coverage interval means "not yet determined" -- never
    "was not recording".
    """

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "kind": self.kind.value,
            "system": self.system,
            "site": self.site.to_dict() if self.site else None,
            "band_hz": self.band_hz.to_dict() if self.band_hz else None,
            "active": self.active,
            "known_range": self.known_range.to_dict() if self.known_range else None,
        }


@dataclass
class Source:
    id: str
    name: str
    kind: SourceKind
    url: str | None = None
    attribution: str | None = None
    fetched_at: datetime | None = None
    status: SourceStatus = SourceStatus.OK
    detail: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "kind": self.kind.value,
            "url": self.url,
            "attribution": self.attribution,
            "fetched_at": iso(self.fetched_at),
            "status": self.status.value,
            "detail": self.detail,
        }


@dataclass
class CoverageInterval:
    instrument_id: str
    start: datetime
    end: datetime
    quality: Quality = Quality.GOOD
    note: str | None = None
    source_id: str | None = None

    def __post_init__(self) -> None:
        self.start = ensure_utc(self.start)
        self.end = ensure_utc(self.end)
        if self.end <= self.start:
            raise ValueError(
                f"coverage for {self.instrument_id} ends at or before it starts: "
                f"{iso(self.start)} .. {iso(self.end)}"
            )

    @property
    def span(self) -> Span:
        return Span(self.start, self.end)

    def clipped_to(self, window: Span) -> "CoverageInterval | None":
        clipped = self.span.intersection(window)
        if clipped is None or clipped.is_instant:
            return None
        return CoverageInterval(
            instrument_id=self.instrument_id,
            start=clipped.start,
            end=clipped.end,
            quality=self.quality,
            note=self.note,
            source_id=self.source_id,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "instrument_id": self.instrument_id,
            "start": iso(self.start),
            "end": iso(self.end),
            "quality": self.quality.value,
            "note": self.note,
            "source_id": self.source_id,
        }


@dataclass(frozen=True)
class Segment:
    """A derived stretch during which a fixed set of instruments was recording."""

    start: datetime
    end: datetime
    instrument_ids: tuple[str, ...]
    min_quality: Quality

    @property
    def degree(self) -> int:
        return len(self.instrument_ids)

    @property
    def span(self) -> Span:
        return Span(self.start, self.end)

    def to_dict(self) -> dict[str, Any]:
        return {
            "start": iso(self.start),
            "end": iso(self.end),
            "instrument_ids": list(self.instrument_ids),
            "degree": self.degree,
            "min_quality": self.min_quality.value,
        }


@dataclass(frozen=True)
class EventLocation:
    label: str | None = None
    latitude: float | None = None
    longitude: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "label": self.label,
            "latitude": self.latitude,
            "longitude": self.longitude,
        }


@dataclass
class Event:
    id: str
    source_id: str
    kind: EventKind
    time: datetime
    source_ref: str | None = None
    end_time: datetime | None = None
    time_uncertainty_s: float | None = None
    label: str | None = None
    location: EventLocation | None = None
    magnitude: float | None = None
    duration_s: float | None = None
    witness_count: int | None = None
    """How many independent reports the source holds for this event.

    Carried because it is the source's own measure of how well attested an event is: a
    magnitude averaged from two eyewitnesses and one averaged from forty are not the same claim.
    """
    url: str | None = None

    def __post_init__(self) -> None:
        self.time = ensure_utc(self.time)
        if self.end_time is not None:
            self.end_time = ensure_utc(self.end_time)
            if self.end_time < self.time:
                raise ValueError(f"event {self.id} ends before it begins")

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "source_id": self.source_id,
            "source_ref": self.source_ref,
            "kind": self.kind.value,
            "time": iso(self.time),
            "end_time": iso(self.end_time),
            "time_uncertainty_s": self.time_uncertainty_s,
            "label": self.label,
            "location": self.location.to_dict() if self.location else None,
            "magnitude": self.magnitude,
            "duration_s": self.duration_s,
            "witness_count": self.witness_count,
            "url": self.url,
        }


@dataclass(frozen=True)
class CoveringInstrument:
    instrument_id: str
    quality: Quality

    def to_dict(self) -> dict[str, Any]:
        return {"instrument_id": self.instrument_id, "quality": self.quality.value}


@dataclass(frozen=True)
class EventCoverage:
    event_id: str
    verdict: Verdict
    covering: tuple[CoveringInstrument, ...] = field(default_factory=tuple)

    @property
    def degree(self) -> int:
        return len(self.covering)

    def to_dict(self) -> dict[str, Any]:
        return {
            "event_id": self.event_id,
            "verdict": self.verdict.value,
            "degree": self.degree,
            "covering": [c.to_dict() for c in self.covering],
        }
