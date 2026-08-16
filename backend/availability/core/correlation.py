"""Answer the question the whole project exists to answer.

Given an event and our coverage record, were we recording? The distinction that matters most here is
between :attr:`Verdict.NOT_COVERED` -- we know we were down -- and :attr:`Verdict.UNKNOWN` -- we have
never characterised that period. Collapsing the second into the first would overstate what we know
about our own instruments.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import timedelta
from typing import Iterable, Sequence

from ..models import (
    CoverageInterval,
    CoveringInstrument,
    Event,
    EventCoverage,
    Instrument,
    Quality,
    Span,
    Verdict,
)
from .intervals import covers_fully


def event_window(event: Event) -> Span:
    """The span an event must be measured against.

    A point event with no stated uncertainty is an instant. Timing uncertainty widens the window
    symmetrically, because a fireball reported to the minute may have occurred either side of it.
    """
    start = event.time
    end = event.end_time or event.time
    if event.time_uncertainty_s:
        padding = timedelta(seconds=float(event.time_uncertainty_s))
        start -= padding
        end += padding
    return Span(start, end)


class CoverageIndex:
    """Coverage grouped by instrument, for repeated window queries."""

    def __init__(self, coverage: Iterable[CoverageInterval]) -> None:
        self._by_instrument: dict[str, list[CoverageInterval]] = defaultdict(list)
        for record in coverage:
            self._by_instrument[record.instrument_id].append(record)
        for records in self._by_instrument.values():
            records.sort(key=lambda record: (record.start, record.end))

    @property
    def instrument_ids(self) -> list[str]:
        return sorted(self._by_instrument)

    def touching(self, window: Span) -> dict[str, list[CoverageInterval]]:
        """Every coverage record that shares time with *window*, grouped by instrument."""
        hits: dict[str, list[CoverageInterval]] = {}
        for instrument_id, records in self._by_instrument.items():
            matched = [record for record in records if record.span.overlaps(window)]
            if matched:
                hits[instrument_id] = matched
        return hits

    def recording_at(self, moment) -> list[CoverageInterval]:
        """Coverage records live at *moment*, for live status."""
        instant = Span(moment, moment)
        live: list[CoverageInterval] = []
        for records in self._by_instrument.values():
            live.extend(record for record in records if record.span.overlaps(instant))
        return live


def classify_event(
    event: Event,
    index: CoverageIndex,
    instruments: Sequence[Instrument],
) -> EventCoverage:
    """Decide whether *event* was captured, and by what."""
    window = event_window(event)
    hits = index.touching(window)

    covering: list[CoveringInstrument] = []
    fully_covered_clean = False
    for instrument_id, records in sorted(hits.items()):
        covering.append(
            CoveringInstrument(
                instrument_id=instrument_id,
                quality=Quality.worst(record.quality for record in records),
            )
        )
        clean = [record.span for record in records if record.quality is Quality.GOOD]
        if clean and covers_fully(clean, window):
            fully_covered_clean = True

    if fully_covered_clean:
        verdict = Verdict.COVERED
    elif covering:
        verdict = Verdict.PARTIAL
    elif _inside_any_known_range(window, instruments):
        verdict = Verdict.NOT_COVERED
    else:
        verdict = Verdict.UNKNOWN

    return EventCoverage(event_id=event.id, verdict=verdict, covering=tuple(covering))


def classify_events(
    events: Iterable[Event],
    coverage: Iterable[CoverageInterval],
    instruments: Sequence[Instrument],
) -> list[EventCoverage]:
    index = CoverageIndex(coverage)
    return [classify_event(event, index, instruments) for event in events]


def _inside_any_known_range(window: Span, instruments: Sequence[Instrument]) -> bool:
    """True when at least one instrument claims to have characterised this period."""
    return any(
        instrument.known_range is not None and instrument.known_range.overlaps(window)
        for instrument in instruments
    )
