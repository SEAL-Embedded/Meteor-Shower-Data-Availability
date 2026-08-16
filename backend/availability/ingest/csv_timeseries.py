"""Coverage from raw sample-series CSVs, as NimbusTrace writes them.

Each file is one capture: a header naming the columns, then one row per sample. They are large --
tens of megabytes for a few seconds at a hundred kilosamples a second -- so this reads only the
first and last rows and never the body. Scanning a season's worth of captures should cost seconds,
not hours, and nothing here needs the sample values.

The recording's start comes from the filename; its length comes from the elapsed time between the
first and last rows. That makes the epoch of the time column irrelevant, which matters because it
is not obviously wall-clock: the sample file counts from 93600, which is 26 hours and so cannot be
a time of day.
"""

from __future__ import annotations

import re
from datetime import timedelta
from pathlib import Path

from ..models import CoverageInterval, Quality, SourceKind, SourceStatus, Span, parse_time
from .base import Adapter, CoverageResult, register
from .file_scan import DEFAULT_GAP_TOLERANCE_S, _timestamp_from_name

DEFAULT_GLOB = "**/*.csv"
TAIL_BYTES = 64 * 1024
_NUMBER = re.compile(r"-?\d+(?:\.\d+)?(?:[eE][-+]?\d+)?")


@register("csv_timeseries")
class CsvTimeSeriesAdapter(Adapter):
    """Derive coverage from a directory of raw sample-series captures.

    Options:

    ``path``
        Directory to scan, or a list of candidate directories of which the first that exists is
        used. Required. A list is the answer to a data drive whose letter is not guaranteed.
    ``instrument_id``
        Which instrument wrote these files. Required.
    ``glob``
        Which files to consider. Defaults to ``**/*.csv``.
    ``duration_s``
        Capture length, when every file is the same length. Set it: the files are then never
        opened, which is what makes scanning tens of thousands of captures quick.
    ``time_column``
        Index of the elapsed-time column, counting from zero. Defaults to 0. Only read when
        ``duration_s`` is not set.
    ``time_unit``
        ``s`` (default), ``ms`` or ``us`` -- the unit of that column.
    ``timestamp_regex`` / ``timestamp_format``
        How to read the start time out of the filename. The common conventions, including
        ``data-2026-08-13T03-34-48-474405.csv``, are recognised without configuration.
    ``gap_tolerance_s``
        Consecutive captures closer together than this are treated as one unbroken recording.
    ``folder_quality``
        Maps a marker in the containing folder's name to a quality, so a session that was cut
        short is not recorded as whole::

            folder_quality = { "_completely_saved" = "good", "_break_saved" = "degraded" }

        A folder matching none of the markers falls back to ``quality`` and is named in the
        source detail, so an unrecognised convention shows up rather than passing as clean.
    ``quality``
        Quality for captures with no folder marker. Defaults to ``good``.
    """

    kind = SourceKind.COVERAGE

    def fetch(self) -> CoverageResult:
        directory, tried = self.resolve_directory()
        instrument_id = self.required_option("instrument_id")
        if directory is None:
            return CoverageResult(
                source=self.describe(
                    SourceStatus.ERROR, "no data directory found; tried " + ", ".join(tried)
                )
            )

        pattern = self.option("glob", DEFAULT_GLOB)
        matcher = self.option("timestamp_regex")
        compiled = re.compile(matcher) if matcher else None
        time_format = self.option("timestamp_format")
        column = int(self.option("time_column", 0))
        scale = {"s": 1.0, "ms": 1e-3, "us": 1e-6}[str(self.option("time_unit", "s")).lower()]

        # A fixed capture length means the files never have to be opened at all. Continuous
        # ten-second captures run to thousands of files a day, and reading each one's ends to
        # rediscover a length you already know is the difference between seconds and hours.
        fixed = self.option("duration_s")
        quality_map = {str(k): Quality(str(v).lower()) for k, v in
                       (self.option("folder_quality") or {}).items()}
        fallback = Quality(str(self.option("quality", Quality.GOOD.value)).lower())

        captures: list[tuple] = []
        unreadable: list[str] = []
        unlabelled: set[str] = set()
        for path in sorted(directory.glob(pattern)):
            if not path.is_file():
                continue
            try:
                started = _timestamp_from_name(path.name, compiled, time_format)
                duration = float(fixed) if fixed else _duration_s(path, column) * scale
            except ValueError as exc:
                unreadable.append(f"{path.name}: {exc}")
                continue
            if duration <= 0:
                unreadable.append(f"{path.name}: no elapsed time between first and last sample")
                continue

            quality = _folder_quality(path.parent.name, quality_map)
            if quality is None:
                quality = fallback
                if quality_map:
                    unlabelled.add(path.parent.name)
            captures.append((started, duration, quality))

        if not captures:
            detail = (
                f"{len(unreadable)} file(s) unreadable: " + "; ".join(unreadable[:5])
                if unreadable
                else f"no files matched {pattern!r} under {directory}"
            )
            return CoverageResult(source=self.describe(SourceStatus.STALE, detail))

        captures.sort(key=lambda c: c[0])
        intervals = self._stitch(captures, instrument_id)
        known = Span(
            captures[0][0],
            max(start + timedelta(seconds=length) for start, length, _ in captures),
        )

        notes = []
        if unreadable:
            notes.append(f"{len(unreadable)} file(s) skipped: " + "; ".join(unreadable[:5]))
        if unlabelled:
            notes.append(
                f"{len(unlabelled)} folder(s) carried no completeness marker and were taken as "
                f"{fallback.value}: " + ", ".join(sorted(unlabelled)[:3])
            )
        notes.append(f"{len(captures)} capture(s)")
        status = SourceStatus.STALE if (unreadable or unlabelled) else SourceStatus.OK
        detail = "; ".join(notes)
        return CoverageResult(
            source=self.describe(status, detail),
            intervals=intervals,
            known_ranges={instrument_id: known},
        )

    def _stitch(self, captures: list[tuple], instrument_id: str) -> list[CoverageInterval]:
        """Join consecutive captures, breaking on a real gap or a change of quality.

        A session that saved completely and one that was interrupted must not merge into a single
        interval claiming the better of the two.
        """
        tolerance = timedelta(
            seconds=float(self.option("gap_tolerance_s", DEFAULT_GAP_TOLERANCE_S))
        )
        source_id = self.source_config.id
        intervals: list[CoverageInterval] = []
        run_start = run_end = run_quality = None

        def close():
            if run_start is not None:
                intervals.append(
                    CoverageInterval(
                        instrument_id, run_start, run_end, run_quality, None, source_id
                    )
                )

        for started, duration, quality in captures:
            finished = started + timedelta(seconds=duration)
            if run_start is None:
                run_start, run_end, run_quality = started, finished, quality
            elif started - run_end <= tolerance and quality is run_quality:
                run_end = max(run_end, finished)
            else:
                close()
                run_start, run_end, run_quality = started, finished, quality

        close()
        return intervals


def _folder_quality(folder: str, mapping: dict[str, Quality]) -> Quality | None:
    """Read a session's completeness off its folder name.

    The writer records how a session ended -- ``..._completely_saved`` against
    ``..._break_saved`` -- and that is a statement about whether the data is whole. Longest
    marker first, so a specific suffix is not shadowed by a shorter one that also matches.
    """
    for marker in sorted(mapping, key=len, reverse=True):
        if marker in folder:
            return mapping[marker]
    return None


def _duration_s(path: Path, column: int) -> float:
    """Elapsed time from first sample to last, in one pass over the ends of the file.

    Deliberately a single open: these files live on a synced drive where the first read of a
    capture can cost seconds, and opening twice paid that twice.
    """
    with path.open("rb") as handle:
        header = handle.readline()
        if not header:
            raise ValueError("empty file")
        first = handle.readline()
        second = handle.readline()
        if not first:
            raise ValueError("no data rows")

        size = path.stat().st_size
        handle.seek(max(0, size - TAIL_BYTES))
        tail = handle.read().splitlines()

    last = next((line for line in reversed(tail) if _cell(line, column) is not None), None)
    if last is None:
        raise ValueError("no readable time column near the end of the file")

    start, end = _cell(first, column), _cell(last, column)
    if start is None:
        raise ValueError(f"column {column} of the first row is not a number")
    if end < start:
        raise ValueError("the time column decreases from first row to last")

    # The last row marks the final sample's start, not the end of the recording, so the capture
    # runs one sample period longer than the span between first and last.
    follower = _cell(second, column)
    period = follower - start if follower is not None and follower > start else 0.0
    return (end - start) + period


def _cell(line: bytes | None, column: int) -> float | None:
    if not line:
        return None
    parts = line.decode("utf-8", "replace").strip().split(",")
    if column >= len(parts):
        return None
    found = _NUMBER.match(parts[column].strip())
    return float(found.group(0)) if found else None
