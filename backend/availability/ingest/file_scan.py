"""Derive coverage from a directory of recordings.

This is the primary adapter for an instrument that writes timestamped files, NimbusTrace included.
It does not care what the instrument is -- it reads when each file starts and how long it runs, then
stitches consecutive files into continuous coverage.

Filename timestamps are read as UTC. An instrument writing local-time filenames must be fixed at the
instrument, not compensated for here; a configurable offset would silently become the place every
future timezone bug hides.
"""

from __future__ import annotations

import re
import struct
from datetime import datetime, timedelta, timezone
from pathlib import Path

from ..models import CoverageInterval, Quality, SourceKind, SourceStatus, Span, parse_time
from .base import Adapter, CoverageResult, register

DEFAULT_GLOB = "**/*.wav"
DEFAULT_GAP_TOLERANCE_S = 60.0


@register("file_scan")
class FileScanAdapter(Adapter):
    """Build coverage intervals from timestamped recording files.

    Options:

    ``path``
        Directory to scan, or a list of candidate directories of which the first that exists is
        used. Required. A list is the answer to a data drive whose letter is not guaranteed.
    ``instrument_id``
        Which instrument these files belong to. Required.
    ``glob``
        Which files to consider. Defaults to ``**/*.wav``.
    ``timestamp_source``
        ``filename`` (default) or ``folder``. Use ``folder`` when the capture time is on the
        containing directory rather than the file, as with ``SuperSID-0813T12-03-00/``.
    ``year``
        The year, for names that give only a month and day. Without it the file's modification
        time is used, which is right in place and wrong for an archive copied later.
    ``timestamp_regex``
        Regular expression with a named group ``ts`` locating the timestamp within the name.
        Only needed for a naming convention not in :data:`_FALLBACKS`; requires
        ``timestamp_format`` alongside it.
    ``timestamp_format``
        :func:`time.strptime` format for that group.
    ``duration_source``
        ``wav`` to read the length from the file header, or ``fixed`` to use ``duration_s``.
        The WAV reader handles IEEE float and RF64 as well as plain integer PCM.
    ``duration_s``
        Recording length in seconds, when ``duration_source`` is ``fixed``.
    ``gap_tolerance_s``
        Consecutive files closer together than this are treated as one unbroken recording.
        Defaults to 60 seconds, which absorbs file-rollover latency without papering over a
        genuine dropout.
    ``quality``
        Quality to assign. Defaults to ``good``; a scan cannot tell impaired data from clean data.
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
        recordings, unreadable = self._read_recordings(directory, pattern)
        if not recordings:
            detail = f"no files matched {pattern!r} under {directory}"
            return CoverageResult(source=self.describe(SourceStatus.STALE, detail))

        intervals = self._stitch(recordings, instrument_id)
        known_ranges = {instrument_id: self._known_range(recordings)}
        status, detail = (
            (SourceStatus.OK, None)
            if not unreadable
            else (
                SourceStatus.STALE,
                f"{len(unreadable)} file(s) skipped: " + "; ".join(unreadable[:5]),
            )
        )
        return CoverageResult(
            source=self.describe(status, detail),
            intervals=intervals,
            known_ranges=known_ranges,
        )

    # -- reading ---------------------------------------------------------------------------

    def _read_recordings(
        self, directory: Path, pattern: str
    ) -> tuple[list[tuple[datetime, float]], list[str]]:
        matcher = self.option("timestamp_regex")
        compiled = re.compile(matcher) if matcher else None
        time_format = self.option("timestamp_format")

        from_folder = str(self.option("timestamp_source", "filename")).lower() == "folder"
        year = self.option("year")

        recordings: list[tuple[datetime, float]] = []
        unreadable: list[str] = []
        for path in sorted(directory.glob(pattern)):
            if not path.is_file():
                continue
            label = path.parent.name if from_folder else path.name
            try:
                started = _timestamp_from_name(label, compiled, time_format)
                started = _with_year(started, year, path, label)
                duration = self._duration(path)
            except ValueError as exc:
                unreadable.append(f"{label}: {exc}")
                continue
            if duration <= 0:
                unreadable.append(f"{path.name}: zero-length recording")
                continue
            recordings.append((started, duration))

        recordings.sort()
        return recordings, unreadable

    def _duration(self, path: Path) -> float:
        source = str(self.option("duration_source", "wav")).lower()
        if source == "fixed":
            fixed = self.option("duration_s")
            if fixed is None:
                raise ValueError("duration_source is 'fixed' but no duration_s was configured")
            return float(fixed)
        if source == "wav":
            return _wav_duration_s(path)
        raise ValueError(f"unknown duration_source {source!r}; expected 'wav' or 'fixed'")

    # -- stitching -------------------------------------------------------------------------

    def _stitch(
        self, recordings: list[tuple[datetime, float]], instrument_id: str
    ) -> list[CoverageInterval]:
        tolerance = timedelta(seconds=float(self.option("gap_tolerance_s", DEFAULT_GAP_TOLERANCE_S)))
        quality = Quality(str(self.option("quality", Quality.GOOD.value)).lower())

        intervals: list[CoverageInterval] = []
        run_start, run_end = None, None
        for started, duration in recordings:
            finished = started + timedelta(seconds=duration)
            if run_start is None:
                run_start, run_end = started, finished
                continue
            if started - run_end <= tolerance:
                run_end = max(run_end, finished)
                continue
            intervals.append(self._interval(instrument_id, run_start, run_end, quality))
            run_start, run_end = started, finished

        if run_start is not None and run_end is not None:
            intervals.append(self._interval(instrument_id, run_start, run_end, quality))
        return intervals

    def _interval(
        self, instrument_id: str, start: datetime, end: datetime, quality: Quality
    ) -> CoverageInterval:
        return CoverageInterval(
            instrument_id=instrument_id,
            start=start,
            end=end,
            quality=quality,
            source_id=self.source_config.id,
        )

    def _known_range(self, recordings: list[tuple[datetime, float]]) -> Span:
        declared = self.option("known_range")
        if declared:
            return Span(parse_time(declared["start"]), parse_time(declared["end"]))
        first_start = recordings[0][0]
        last_end = max(started + timedelta(seconds=duration) for started, duration in recordings)
        return Span(first_start, last_end)


# ----------------------------------------------------------------------------------------
# helpers
# ----------------------------------------------------------------------------------------

#: Naming conventions understood without configuration, most specific first. An instrument that
#: names files some other way needs ``timestamp_regex`` and ``timestamp_format`` set explicitly.
_FALLBACKS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"(?P<ts>\d{4}-\d{2}-\d{2}T\d{2}-\d{2}-\d{2}-\d{6})"), "%Y-%m-%dT%H-%M-%S-%f"),
    (re.compile(r"(?P<ts>\d{4}-\d{2}-\d{2}T\d{2}-\d{2}-\d{2})"), "%Y-%m-%dT%H-%M-%S"),
    (re.compile(r"(?P<ts>\d{4}-\d{2}-\d{2}_\d{2}-\d{2}-\d{2})"), "%Y-%m-%d_%H-%M-%S"),
    (re.compile(r"(?P<ts>\d{8}_\d{6})"), "%Y%m%d_%H%M%S"),
    (re.compile(r"(?P<ts>\d{8}T\d{6})"), "%Y%m%dT%H%M%S"),
    (re.compile(r"(?P<ts>\d{8}-\d{6})"), "%Y%m%d-%H%M%S"),
    (re.compile(r"(?P<ts>\d{14})"), "%Y%m%d%H%M%S"),
    # Month and day only, no year -- as in SuperSID-0813T12-03-00. Last, so a name that does
    # carry a year is never truncated to this by accident.
    (re.compile(r"(?P<ts>\d{4}T\d{2}-\d{2}-\d{2})"), "%m%dT%H-%M-%S"),
)

#: strptime's year when the pattern supplied none.
NO_YEAR = 1900


def _timestamp_from_name(
    name: str, matcher: re.Pattern[str] | None, time_format: str | None
) -> datetime:
    if matcher is not None:
        if time_format is None:
            raise ValueError("timestamp_regex was set without a timestamp_format")
        return _parse_with(name, matcher, time_format)

    attempts = (
        [(pattern, time_format) for pattern, _ in _FALLBACKS]
        if time_format is not None
        else list(_FALLBACKS)
    )
    for pattern, fmt in attempts:
        try:
            return _parse_with(name, pattern, fmt)
        except ValueError:
            continue
    raise ValueError("no recognisable timestamp in the filename")


def _with_year(moment: datetime, configured, path: Path, label: str) -> datetime:
    """Supply the year when the name carries only a month and day.

    A folder called ``SuperSID-0813T12-03-00`` says nothing about which August. The configured
    year wins; failing that the file's own modification time is used, which is right for data
    read where it was written and wrong for an archive copied later -- so configure it for
    anything that has been moved.
    """
    if moment.year != NO_YEAR:
        return moment
    if configured:
        return moment.replace(year=int(configured))
    try:
        return moment.replace(year=datetime.fromtimestamp(path.stat().st_mtime).year)
    except OSError:
        raise ValueError(f"{label!r} has no year and none could be determined") from None


def _parse_with(name: str, pattern: re.Pattern[str], time_format: str) -> datetime:
    found = pattern.search(name)
    if not found:
        raise ValueError(f"no timestamp matching {pattern.pattern!r} in the filename")
    try:
        text = found.group("ts")
    except IndexError:
        raise ValueError("timestamp_regex must define a named group 'ts'") from None
    try:
        parsed = datetime.strptime(text, time_format)
    except ValueError:
        raise ValueError(f"timestamp {text!r} does not match format {time_format!r}") from None
    return parsed.replace(tzinfo=timezone.utc)


def _wav_duration_s(path: Path) -> float:
    """Read a recording's length straight out of the RIFF header.

    The standard library's ``wave`` module handles integer PCM only, and refuses IEEE float
    (format tag 3) outright -- which is what a VLF chain writes. So the chunks are walked here
    instead. That also gets RF64 for free, which matters: at 96 kHz in 32-bit float a capture
    passes the four-gigabyte limit of plain RIFF in under three hours, and a plain reader would
    report a wrapped-around length rather than failing.

    Only the header is read; the audio itself is never touched.
    """
    try:
        with path.open("rb") as handle:
            return _riff_duration_s(handle)
    except OSError as exc:
        raise ValueError(f"cannot open file ({exc})") from None


def _riff_duration_s(handle) -> float:
    header = handle.read(12)
    if len(header) < 12 or header[8:12] != b"WAVE":
        raise ValueError("not a RIFF/WAVE file")
    magic = header[0:4]
    if magic not in (b"RIFF", b"RF64"):
        raise ValueError(f"unrecognised container {magic!r}")

    byte_rate = 0
    data_bytes: int | None = None
    ds64_data_bytes: int | None = None

    while True:
        chunk_header = handle.read(8)
        if len(chunk_header) < 8:
            break
        chunk_id, chunk_size = struct.unpack("<4sI", chunk_header)
        padded = chunk_size + (chunk_size % 2)  # chunks are word-aligned

        if chunk_id == b"data":
            data_bytes = chunk_size
            break

        payload = handle.read(padded)
        if chunk_id == b"ds64" and len(payload) >= 16:
            ds64_data_bytes = struct.unpack("<Q", payload[8:16])[0]
        elif chunk_id == b"fmt ":
            if len(payload) < 16:
                raise ValueError("truncated fmt chunk")
            _, channels, sample_rate, declared_rate, _, bits = struct.unpack(
                "<HHIIHH", payload[:16]
            )
            byte_rate = declared_rate or (sample_rate * channels * bits // 8)
            if not byte_rate:
                raise ValueError("header reports a zero byte rate")

    if data_bytes is None:
        raise ValueError("no data chunk in the header")
    if not byte_rate:
        raise ValueError("no fmt chunk before the data chunk")

    # RF64 parks a sentinel in the 32-bit field and keeps the real size in ds64.
    if data_bytes == 0xFFFFFFFF:
        if ds64_data_bytes is None:
            raise ValueError("RF64 file without a ds64 chunk")
        data_bytes = ds64_data_bytes

    return data_bytes / float(byte_rate)
