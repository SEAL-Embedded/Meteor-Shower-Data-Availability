"""CSV adapters, for records kept by hand.

These exist so a historical record can be published before any automated ingest works, and so a
human correction always has somewhere to live. Files are read as UTF-8 with an optional byte order
mark, because these are routinely exported from spreadsheet software.
"""

from __future__ import annotations

import csv
from pathlib import Path
from typing import Any, Iterator

from ..models import (
    CoverageInterval,
    Event,
    EventKind,
    EventLocation,
    Quality,
    SourceKind,
    SourceStatus,
    Span,
    parse_time,
)
from .base import Adapter, CoverageResult, EventResult, register, span_of

COVERAGE_COLUMNS = ("instrument_id", "start", "end", "quality", "note")
EVENT_COLUMNS = (
    "id",
    "kind",
    "time",
    "end_time",
    "time_uncertainty_s",
    "label",
    "location",
    "latitude",
    "longitude",
    "magnitude",
    "duration_s",
    "url",
    "source_ref",
)


@register("csv_coverage")
class CsvCoverageAdapter(Adapter):
    """Read coverage intervals from a CSV.

    Columns: ``instrument_id``, ``start``, ``end``, and optionally ``quality`` and ``note``.
    Timestamps are ISO 8601 with an explicit UTC marker.

    By default the characterised period is inferred as the span of the rows themselves, which
    means gaps between rows read as downtime and anything outside them reads as unexamined. Set
    ``known_range`` explicitly when the file asserts more than its rows show.
    """

    kind = SourceKind.COVERAGE

    def fetch(self) -> CoverageResult:
        path = self.config.resolve(self.required_option("path"))
        if not path.is_file():
            return CoverageResult(
                source=self.describe(SourceStatus.ERROR, f"no such file: {path}")
            )

        intervals: list[CoverageInterval] = []
        problems: list[str] = []
        for line_number, row in _rows(path):
            try:
                intervals.append(
                    CoverageInterval(
                        instrument_id=_require(row, "instrument_id", line_number),
                        start=parse_time(_require(row, "start", line_number)),
                        end=parse_time(_require(row, "end", line_number)),
                        quality=_quality(row.get("quality"), line_number),
                        note=_clean(row.get("note")),
                        source_id=self.source_config.id,
                    )
                )
            except (ValueError, KeyError) as exc:
                problems.append(f"line {line_number}: {exc}")

        known_ranges = self._known_ranges(intervals)
        status, detail = _outcome(len(intervals), problems, path)
        return CoverageResult(
            source=self.describe(status, detail),
            intervals=intervals,
            known_ranges=known_ranges,
        )

    def _known_ranges(self, intervals: list[CoverageInterval]) -> dict[str, Span]:
        declared = self.option("known_range")
        if declared:
            window = Span(parse_time(declared["start"]), parse_time(declared["end"]))
            return {record.instrument_id: window for record in intervals}

        ranges: dict[str, Span] = {}
        for instrument_id in {record.instrument_id for record in intervals}:
            span = span_of(r for r in intervals if r.instrument_id == instrument_id)
            if span is not None:
                ranges[instrument_id] = span
        return ranges


@register("csv_events")
class CsvEventAdapter(Adapter):
    """Read events from a CSV.

    Required columns: ``kind`` and ``time``. Everything in :data:`EVENT_COLUMNS` is accepted;
    unknown columns are ignored. Rows without an ``id`` are given one derived from the source and
    the event time, so re-importing the same file does not duplicate records.
    """

    kind = SourceKind.EVENT

    def fetch(self) -> EventResult:
        path = self.config.resolve(self.required_option("path"))
        if not path.is_file():
            return EventResult(source=self.describe(SourceStatus.ERROR, f"no such file: {path}"))

        events: list[Event] = []
        problems: list[str] = []
        for line_number, row in _rows(path):
            try:
                events.append(self._event(row, line_number))
            except (ValueError, KeyError) as exc:
                problems.append(f"line {line_number}: {exc}")

        status, detail = _outcome(len(events), problems, path)
        return EventResult(source=self.describe(status, detail), events=events)

    def _event(self, row: dict[str, str], line_number: int) -> Event:
        moment = parse_time(_require(row, "time", line_number))
        source_ref = _clean(row.get("source_ref"))
        identifier = _clean(row.get("id")) or "{}-{}".format(
            self.source_config.id, source_ref or moment.strftime("%Y%m%dT%H%M%SZ")
        )
        location_label = _clean(row.get("location"))
        latitude = _number(row.get("latitude"))
        longitude = _number(row.get("longitude"))
        end_time = _clean(row.get("end_time"))

        return Event(
            id=identifier,
            source_id=self.source_config.id,
            source_ref=source_ref,
            kind=_event_kind(row.get("kind"), line_number),
            time=moment,
            end_time=parse_time(end_time) if end_time else None,
            time_uncertainty_s=_number(row.get("time_uncertainty_s")),
            label=_clean(row.get("label")),
            location=(
                EventLocation(label=location_label, latitude=latitude, longitude=longitude)
                if (location_label or latitude is not None or longitude is not None)
                else None
            ),
            magnitude=_number(row.get("magnitude")),
            duration_s=_number(row.get("duration_s")),
            url=_clean(row.get("url")),
        )


# ----------------------------------------------------------------------------------------
# shared parsing helpers
# ----------------------------------------------------------------------------------------


def _rows(path: Path) -> Iterator[tuple[int, dict[str, str]]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            if not any((value or "").strip() for value in row.values()):
                continue
            yield reader.line_num, {(key or "").strip(): value for key, value in row.items()}


def _require(row: dict[str, str], column: str, line_number: int) -> str:
    value = _clean(row.get(column))
    if not value:
        raise ValueError(f"column {column!r} is required but empty")
    return value


def _clean(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _number(value: Any) -> float | None:
    text = _clean(value)
    if text is None:
        return None
    return float(text)


def _quality(value: Any, line_number: int) -> Quality:
    text = _clean(value)
    if text is None:
        return Quality.GOOD
    try:
        return Quality(text.lower())
    except ValueError:
        raise ValueError(
            f"unknown quality {text!r}; expected one of {[q.value for q in Quality]}"
        ) from None


def _event_kind(value: Any, line_number: int) -> EventKind:
    text = _clean(value)
    if text is None:
        return EventKind.OTHER
    try:
        return EventKind(text.lower())
    except ValueError:
        raise ValueError(
            f"unknown event kind {text!r}; expected one of {[k.value for k in EventKind]}"
        ) from None


def _outcome(accepted: int, problems: list[str], path: Path) -> tuple[SourceStatus, str | None]:
    """Partial success is reported, not hidden: rows that failed are named."""
    if not problems:
        return SourceStatus.OK, None
    summary = "; ".join(problems[:5])
    if len(problems) > 5:
        summary += f"; and {len(problems) - 5} more"
    if accepted == 0:
        return SourceStatus.ERROR, f"no usable rows in {path.name}: {summary}"
    return SourceStatus.STALE, f"{len(problems)} row(s) in {path.name} skipped: {summary}"
