"""Coverage from SuperSID daily log files.

A SuperSID monitor writes one CSV per day: a commented header block, then one row per logging
interval. Coverage is derived from which intervals actually carry a sample -- a day file with a
two-hour hole in the middle is two coverage intervals, not one.

A daily file characterises its whole day. That is why this adapter reports a ``known_range`` of the
full day rather than the span of the rows: a missing hour inside a day we logged is downtime we can
assert, not a period we never looked at.

.. warning::
   The header keys below follow the standard SuperSID log layout. Validate this adapter against
   real output from your own monitor before trusting a published record built from it -- run
   ``python -m availability check`` and compare the reported intervals against a day you know.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

from ..models import CoverageInterval, Quality, SourceKind, SourceStatus, Span, parse_time
from .base import Adapter, CoverageResult, register

DEFAULT_GLOB = "**/*.csv"
DEFAULT_LOG_INTERVAL_S = 5.0
DEFAULT_GAP_FACTOR = 2.0
_HEADER_MARKER = "#"
_TIME_FORMATS = ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d")


@register("supersid")
class SuperSidAdapter(Adapter):
    """Read SuperSID daily logs into coverage intervals.

    Options:

    ``path``
        Directory of daily log files. Required.
    ``instrument_id``
        Which instrument these logs belong to. Required.
    ``glob``
        Which files to read. Defaults to ``**/*.csv``.
    ``gap_factor``
        A gap longer than ``gap_factor`` logging intervals splits the coverage. Defaults to 2,
        so a single dropped sample does not fragment a day.
    ``day_hours``
        How much time a single file is taken to characterise. Defaults to 24.
    """

    kind = SourceKind.COVERAGE

    def fetch(self) -> CoverageResult:
        directory = self.config.resolve(self.required_option("path"))
        instrument_id = self.required_option("instrument_id")
        if not directory.is_dir():
            return CoverageResult(
                source=self.describe(SourceStatus.ERROR, f"no such directory: {directory}")
            )

        pattern = self.option("glob", DEFAULT_GLOB)
        gap_factor = float(self.option("gap_factor", DEFAULT_GAP_FACTOR))
        day_hours = float(self.option("day_hours", 24.0))

        intervals: list[CoverageInterval] = []
        characterised: list[Span] = []
        problems: list[str] = []

        for path in sorted(directory.glob(pattern)):
            if not path.is_file():
                continue
            try:
                samples, day_start, interval_s = _read_log(path)
            except ValueError as exc:
                problems.append(f"{path.name}: {exc}")
                continue
            if not samples:
                problems.append(f"{path.name}: no data rows")
                continue

            intervals.extend(
                _runs_to_coverage(
                    samples,
                    interval_s=interval_s,
                    gap_factor=gap_factor,
                    instrument_id=instrument_id,
                    source_id=self.source_config.id,
                )
            )
            characterised.append(Span(day_start, day_start + timedelta(hours=day_hours)))

        if not intervals and not characterised:
            detail = f"no readable SuperSID logs matching {pattern!r} under {directory}"
            return CoverageResult(source=self.describe(SourceStatus.STALE, detail))

        known_ranges = {}
        if characterised:
            known_ranges[instrument_id] = Span(
                min(span.start for span in characterised),
                max(span.end for span in characterised),
            )

        status, detail = (
            (SourceStatus.OK, None)
            if not problems
            else (
                SourceStatus.STALE,
                f"{len(problems)} log(s) skipped: " + "; ".join(problems[:5]),
            )
        )
        return CoverageResult(
            source=self.describe(status, detail),
            intervals=intervals,
            known_ranges=known_ranges,
        )


# ----------------------------------------------------------------------------------------
# log parsing
# ----------------------------------------------------------------------------------------


def _read_log(path: Path) -> tuple[list[datetime], datetime, float]:
    """Return the timestamps carrying a sample, the file's day start, and its logging interval."""
    header: dict[str, str] = {}
    data_lines: list[str] = []
    for line in path.read_text(encoding="utf-8-sig", errors="replace").splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith(_HEADER_MARKER):
            key, _, value = stripped.lstrip("# ").partition("=")
            if value:
                header[key.strip().lower()] = value.strip()
            continue
        data_lines.append(stripped)

    day_start = _header_time(header)
    interval_s = _header_interval(header)
    interval = timedelta(seconds=interval_s)

    samples: list[datetime] = []
    for index, line in enumerate(data_lines):
        fields = [field.strip() for field in line.split(",")]
        moment = _row_time(fields[0]) if fields else None
        if moment is None:
            if day_start is None:
                raise ValueError("rows carry no timestamp and the header has no UTC_StartTime")
            moment = day_start + interval * index
            values = fields
        else:
            values = fields[1:]
        if not _has_sample(values):
            continue
        samples.append(moment)

    if day_start is None:
        if not samples:
            raise ValueError("no timestamps could be established for this file")
        day_start = samples[0].replace(hour=0, minute=0, second=0, microsecond=0)

    return samples, day_start, interval_s


def _has_sample(values: list[str]) -> bool:
    """A row counts as data only if at least one field is a real number."""
    for value in values:
        if not value:
            continue
        try:
            number = float(value)
        except ValueError:
            continue
        if number == number:  # NaN is never equal to itself
            return True
    return False


def _header_time(header: dict[str, str]) -> datetime | None:
    raw = header.get("utc_starttime") or header.get("utc_start_time")
    if not raw:
        return None
    for pattern in _TIME_FORMATS:
        try:
            return datetime.strptime(raw, pattern).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    try:
        return parse_time(raw)
    except ValueError:
        return None


def _header_interval(header: dict[str, str]) -> float:
    raw = header.get("loginterval") or header.get("log_interval")
    try:
        return float(raw) if raw else DEFAULT_LOG_INTERVAL_S
    except ValueError:
        return DEFAULT_LOG_INTERVAL_S


def _row_time(field: str) -> datetime | None:
    for pattern in _TIME_FORMATS:
        try:
            return datetime.strptime(field, pattern).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None


def _runs_to_coverage(
    samples: list[datetime],
    *,
    interval_s: float,
    gap_factor: float,
    instrument_id: str,
    source_id: str,
) -> list[CoverageInterval]:
    """Group consecutive samples into coverage, splitting where samples are missing."""
    step = timedelta(seconds=interval_s)
    tolerance = step * gap_factor
    intervals: list[CoverageInterval] = []

    run_start = run_last = None
    for moment in sorted(samples):
        if run_start is None:
            run_start = run_last = moment
            continue
        if moment - run_last <= tolerance:
            run_last = moment
            continue
        intervals.append(
            CoverageInterval(
                instrument_id=instrument_id,
                start=run_start,
                end=run_last + step,
                quality=Quality.GOOD,
                source_id=source_id,
            )
        )
        run_start = run_last = moment

    if run_start is not None and run_last is not None:
        intervals.append(
            CoverageInterval(
                instrument_id=instrument_id,
                start=run_start,
                end=run_last + step,
                quality=Quality.GOOD,
                source_id=source_id,
            )
        )
    return intervals
