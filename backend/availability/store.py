"""Assemble the availability record from every configured source.

The store is the single place where raw ingest becomes the derived products -- overlap segments and
event verdicts. Both output modes read it, so the published snapshot and the live API can never
disagree about what happened.
"""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Iterable

from .config import Config, SourceConfig
from .core.corrections import Corrections
from .core.corrections import load as load_corrections
from .core.correlation import CoverageIndex, classify_events
from .core.intervals import build_segments, clip_segments
from .ingest.base import (
    Adapter,
    AdapterError,
    CoverageResult,
    EventResult,
    empty_result,
    get_adapter,
)
from .models import (
    CoverageInterval,
    Event,
    EventCoverage,
    Instrument,
    Segment,
    Source,
    SourceStatus,
    Span,
    iso,
    now,
)


@dataclass
class Store:
    instruments: list[Instrument]
    coverage: list[CoverageInterval]
    events: list[Event]
    sources: list[Source]
    generated_at: datetime
    warnings: list[str] = field(default_factory=list)
    corrections: Corrections = field(default_factory=Corrections)
    """Reviewed human corrections, merged over the record when the dashboard dataset is written.

    They are held rather than applied here because they speak the dashboard's field names, not the
    record's: the record says what the sources said, and stays that way.
    """
    segments: list[Segment] = field(default_factory=list, init=False)
    event_coverage: list[EventCoverage] = field(default_factory=list, init=False)

    def __post_init__(self) -> None:
        self.coverage.sort(key=lambda record: (record.start, record.instrument_id))
        self.events.sort(key=lambda event: (event.time, event.id))
        self.segments = build_segments(self.coverage)
        self.event_coverage = classify_events(self.events, self.coverage, self.instruments)

    # -- construction ----------------------------------------------------------------------

    @classmethod
    def build(cls, config: Config) -> "Store":
        instruments = [dataclasses.replace(instrument) for instrument in config.instruments]
        known_ids = {instrument.id for instrument in instruments}

        coverage: list[CoverageInterval] = []
        events: list[Event] = []
        sources: list[Source] = []
        warnings: list[str] = []
        declared_ranges: dict[str, list[Span]] = {}

        for source_config in config.sources:
            if not source_config.enabled:
                sources.append(_disabled(source_config))
                continue

            result = _run_adapter(config, source_config)
            sources.append(result.source)

            if isinstance(result, CoverageResult):
                for record in result.intervals:
                    if record.instrument_id not in known_ids:
                        warnings.append(
                            f"source {source_config.id!r} produced coverage for unknown "
                            f"instrument {record.instrument_id!r}; the record was dropped"
                        )
                        continue
                    coverage.append(record)
                for instrument_id, span in result.known_ranges.items():
                    if instrument_id in known_ids:
                        declared_ranges.setdefault(instrument_id, []).append(span)
            else:
                events.extend(result.events)

        _apply_known_ranges(instruments, declared_ranges, coverage)
        _reject_duplicate_events(events, warnings)
        if config.events_within_coverage:
            _restrict_events_to_coverage(events, instruments, warnings)

        corrections = Corrections()
        if config.corrections_path is not None:
            corrections = load_corrections(config.resolve(config.corrections_path))
            warnings.extend(corrections.warnings)

        return cls(
            instruments=instruments,
            coverage=coverage,
            events=events,
            sources=sources,
            generated_at=now(),
            warnings=warnings,
            corrections=corrections,
        )

    # -- derived views ---------------------------------------------------------------------

    @property
    def instrument_map(self) -> dict[str, Instrument]:
        return {instrument.id: instrument for instrument in self.instruments}

    @property
    def range(self) -> Span | None:
        """The union of every instrument's characterised period."""
        ranges = [i.known_range for i in self.instruments if i.known_range is not None]
        if not ranges:
            return None
        return Span(min(r.start for r in ranges), max(r.end for r in ranges))

    @property
    def full_range(self) -> Span | None:
        """Every moment the record says anything about, coverage and events alike.

        Wider than :attr:`range`, which covers only what the instruments characterised. An event
        outside that period is the whole point of the ``unknown`` verdict, so a query for events
        must not default to a window that hides it.
        """
        starts: list[datetime] = []
        ends: list[datetime] = []
        window = self.range
        if window is not None:
            starts.append(window.start)
            ends.append(window.end)
        if self.events:
            starts.append(min(event.time for event in self.events))
            # Windows are half-open, so leave room for an event sitting exactly on the end.
            ends.append(max(event.time for event in self.events) + timedelta(seconds=1))
        if not starts:
            return None
        return Span(min(starts), max(ends))

    @property
    def years(self) -> list[int]:
        window = self.full_range
        if window is None:
            return []
        return list(range(window.start.year, window.end.year + 1))

    def coverage_in(self, window: Span) -> list[CoverageInterval]:
        clipped = (record.clipped_to(window) for record in self.coverage)
        return [record for record in clipped if record is not None]

    def segments_in(self, window: Span) -> list[Segment]:
        return clip_segments(self.segments, window)

    def events_in(self, window: Span) -> list[Event]:
        return [event for event in self.events if window.contains(event.time)]

    def coverage_for(self, events: Iterable[Event]) -> list[EventCoverage]:
        wanted = {event.id for event in events}
        return [record for record in self.event_coverage if record.event_id in wanted]

    def status(self, moment: datetime | None = None) -> dict:
        """What is recording right now."""
        as_of = moment or now()
        live = CoverageIndex(self.coverage).recording_at(as_of)
        recording = [
            {
                "instrument_id": record.instrument_id,
                "quality": record.quality.value,
                "since": record.start,
            }
            for record in sorted(live, key=lambda r: r.instrument_id)
        ]
        return {"as_of": as_of, "recording": recording, "degree": len(recording)}


# --------------------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------------------


def _run_adapter(config: Config, source_config: SourceConfig) -> CoverageResult | EventResult:
    """Run one adapter, converting any failure into a described source rather than a crash."""
    try:
        adapter_class = get_adapter(source_config.adapter)
        adapter: Adapter = adapter_class(config, source_config)
        return adapter.fetch()
    except AdapterError as exc:
        return _failed(source_config, str(exc))
    except Exception as exc:  # noqa: BLE001 - one bad source must not sink the record
        return _failed(source_config, f"{type(exc).__name__}: {exc}")


def _failed(source_config: SourceConfig, detail: str) -> CoverageResult | EventResult:
    return empty_result(source_config, SourceStatus.ERROR, detail)


def _disabled(source_config: SourceConfig) -> Source:
    return Source(
        id=source_config.id,
        name=source_config.name,
        kind=source_config.kind,
        url=source_config.url,
        attribution=source_config.attribution,
        fetched_at=None,
        status=SourceStatus.DISABLED,
        detail="turned off in configuration",
    )


def _apply_known_ranges(
    instruments: list[Instrument],
    declared: dict[str, list[Span]],
    coverage: list[CoverageInterval],
) -> None:
    """Set each instrument's characterised period.

    An adapter's declaration wins where it exists, widened by any coverage that falls outside it.
    Coverage always implies characterisation: if we hold data for a moment, we plainly looked.
    """
    for instrument in instruments:
        spans = list(declared.get(instrument.id, []))
        spans.extend(
            record.span for record in coverage if record.instrument_id == instrument.id
        )
        if not spans:
            instrument.known_range = None
            continue
        instrument.known_range = Span(
            min(span.start for span in spans), max(span.end for span in spans)
        )


def _restrict_events_to_coverage(
    events: list[Event],
    instruments: list[Instrument],
    warnings: list[str],
) -> None:
    """Drop events falling outside every instrument's characterised period.

    A catalogue returns whole years; an event during a month when no instrument had been
    characterised can only ever be reported as ``unknown``, and thousands of them bury the ones
    that can actually be answered. What is dropped is counted rather than discarded quietly.
    """
    ranges = [i.known_range for i in instruments if i.known_range is not None]
    if not ranges:
        return

    window = Span(min(r.start for r in ranges), max(r.end for r in ranges))
    kept = [event for event in events if window.overlaps(Span(event.time, event.time))]
    dropped = len(events) - len(kept)
    if dropped:
        warnings.append(
            f"{dropped} event(s) outside the characterised period "
            f"({iso(window.start)} .. {iso(window.end)}) were not imported"
        )
    events[:] = kept


def _reject_duplicate_events(events: list[Event], warnings: list[str]) -> None:
    """Drop repeated event ids in place, keeping the first and saying so."""
    seen: set[str] = set()
    kept: list[Event] = []
    for event in events:
        if event.id in seen:
            warnings.append(f"duplicate event id {event.id!r}; the later record was dropped")
            continue
        seen.add(event.id)
        kept.append(event)
    events[:] = kept


def year_window(year: int) -> Span:
    """The UTC calendar year *year*, as a span."""
    return Span(
        datetime(year, 1, 1, tzinfo=timezone.utc),
        datetime(year + 1, 1, 1, tzinfo=timezone.utc),
    )
