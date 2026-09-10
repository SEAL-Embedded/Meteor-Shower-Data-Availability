"""Coverage from an archive index: one row per recording folder, already scanned.

The other coverage adapters walk the recordings themselves. This one reads an index somebody else
produced by walking them -- ``coverage_intervals.csv``, written by the archive's own
``correlate_coverage.py`` -- and turns it into the record.

That indirection is deliberate. The raw captures are hundreds of gigabytes on a drive that is not
always mounted and whose letter is not stable, so a publish that has to walk them is a publish that
can only be run in one place. The index is a few kilobytes, it is derived from the folders rather
than from anybody's memory of them, and re-deriving it is one command. Pointing the publisher at it
is what makes a season's coverage reproducible instead of transcribed.

**Sessions are unioned, not summed.** Two recordings can be running at once -- a SuperSID session
was restarted before the previous one stopped, and the two overlap by nearly twelve hours. Adding
their durations claims time that only happened once and takes the duty cycle over 98%; the union
is the honest 95%. Everything this adapter emits tiles its instrument's timeline exactly, so a
reader who sums the published intervals gets the same number the dashboard shows.

**A run is cut where its quality changes, not flattened to the worse of the two.** The archive
records how each session ended -- a ``_completely_saved`` folder against a ``_break_saved`` one --
and that is a statement about which minutes hold whole data, not a grade for the whole run. A
fifteen-minute interrupted session at the end of a twenty-eight-hour recording marks fifteen
minutes as degraded, not twenty-eight hours.

The columns are those ``correlate_coverage.py`` writes: ``instrument``, ``folder``, ``start_utc``,
``end_utc``, ``duration_s``, ``note``. The two timestamp columns carry no zone marker; their names
are the assertion that they are UTC, and they are read that way. ``duration_s`` is redundant with
the other two and is used only as a check -- a row where they disagree says the index is damaged.
"""

from __future__ import annotations

import csv
from datetime import datetime, timezone
from pathlib import Path

from ..core.intervals import merge, subtract
from ..models import CoverageInterval, Quality, SourceKind, SourceStatus, Span
from .base import Adapter, CoverageResult, register

#: How far ``end_utc - start_utc`` may drift from ``duration_s`` before the row is called damaged.
DURATION_TOLERANCE_S = 1.0

#: Said on every interval this adapter produces, in place of the sheet's ``operator_log``: these
#: came from walking the archive, and a reader deserves to know which of the two they are reading.
CHECK_METHOD = "archive_scan"

COLUMNS = ("instrument", "folder", "start_utc", "end_utc", "duration_s", "note")


@register("archive_sessions")
class ArchiveSessionsAdapter(Adapter):
    """Read per-session coverage from an archive index CSV.

    Options:

    ``path``
        The index file, or a list of candidate paths of which the first that exists is used.
        Required. A list is the answer to a data drive whose letter is not guaranteed.
    ``instruments``
        Maps the ``instrument`` column to an instrument id::

            instruments = { NimbusTrace = "nimbustrace-seattle", SuperSID = "supersid-seattle" }

        Required. A row naming an instrument that is not in the map is reported, not guessed at.
    ``clean_note``
        Per instrument, the ``note`` a row carries when its folder saved cleanly::

            clean_note = { NimbusTrace = "completely", SuperSID = "96000Hz/1ch" }

        Anything else is degraded: real data, kept as coverage, marked as not whole. Stating the
        clean value rather than a list of bad ones is the safer way round -- a marker nobody
        anticipated then reads as suspect instead of silently passing as good.
    ``season``
        Optional year. When set, rows outside it are ignored, so one index can serve a season
        without the publisher inheriting whatever else the file grows to hold.
    """

    kind = SourceKind.COVERAGE

    def fetch(self) -> CoverageResult:
        path, tried = self._index_path()
        if path is None:
            return CoverageResult(
                source=self.describe(
                    SourceStatus.ERROR, "no archive index found; tried " + ", ".join(tried)
                )
            )

        instruments = dict(self.required_option("instruments"))
        clean = dict(self.option("clean_note", {}))
        season = self.option("season")

        sessions: dict[str, list[tuple[Span, Quality, str]]] = {}
        problems: list[str] = []
        ungraded: set[str] = set()
        skipped = 0

        for line_number, row in _rows(path):
            label = row.get("instrument", "")
            instrument_id = instruments.get(label)
            if instrument_id is None:
                problems.append(f"line {line_number}: no instrument mapped for {label!r}")
                continue
            try:
                span = _span(row, line_number)
            except ValueError as exc:
                problems.append(f"line {line_number}: {exc}")
                continue
            if season is not None and span.start.year != int(season):
                skipped += 1
                continue

            note = (row.get("note") or "").strip()
            expected = clean.get(label)
            if expected is None:
                # No clean value was stated for this instrument, so nothing here can grade its
                # sessions. Recorded as whole and said out loud below, rather than marking a whole
                # instrument degraded on a technicality.
                ungraded.add(label)
                quality = Quality.GOOD
            else:
                quality = Quality.GOOD if note == expected else Quality.DEGRADED
            sessions.setdefault(instrument_id, []).append((span, quality, note))

        intervals: list[CoverageInterval] = []
        summaries: list[str] = []
        for instrument_id, found in sorted(sessions.items()):
            built = self._intervals(instrument_id, found)
            intervals.extend(built)
            summaries.append(_summary(instrument_id, found, built))

        known_ranges = {
            instrument_id: Span(
                min(span.start for span, _, _ in found), max(span.end for span, _, _ in found)
            )
            for instrument_id, found in sessions.items()
        }

        for label in sorted(ungraded):
            summaries.append(f"{label}: no clean_note configured, so no session was graded")

        status, detail = _outcome(intervals, problems, summaries, skipped, path)
        return CoverageResult(
            source=self.describe(status, detail),
            intervals=intervals,
            known_ranges=known_ranges,
        )

    # -- construction ----------------------------------------------------------------------

    def _index_path(self) -> tuple[Path | None, list[str]]:
        raw = self.required_option("path")
        candidates = [raw] if isinstance(raw, (str, Path)) else list(raw)
        tried: list[str] = []
        for candidate in candidates:
            resolved = self.config.resolve(candidate)
            tried.append(str(resolved))
            if resolved.is_file():
                return resolved, tried
        return None, tried

    def _intervals(
        self, instrument_id: str, sessions: list[tuple[Span, Quality, str]]
    ) -> list[CoverageInterval]:
        """Union the sessions, then cut the union where the degraded ones sit.

        Doing it in that order is what keeps the two properties that matter together: the pieces
        add up to the union exactly, because they are a partition of it, and degraded time stays
        the size the archive says it is, because the cuts are made at the degraded sessions' own
        edges. Overlap between a whole session and an interrupted one resolves to degraded -- the
        same "a chain is as good as its worst link" rule the overlap timeline already applies.
        """
        source_id = self.source_config.id
        spans = [span for span, _, _ in sessions]
        damaged = [span for span, quality, _ in sessions if quality is Quality.DEGRADED]

        def note_over(piece: Span) -> str | None:
            markers = sorted(
                {
                    note or "unmarked"
                    for span, quality, note in sessions
                    if quality is Quality.DEGRADED and span.overlaps(piece)
                }
            )
            return f"save marker: {'; '.join(markers)}" if markers else None

        built: list[CoverageInterval] = []
        for whole in merge(spans):
            for piece in subtract(whole, damaged):
                built.append(
                    CoverageInterval(
                        instrument_id,
                        piece.start,
                        piece.end,
                        Quality.GOOD,
                        None,
                        source_id,
                        CHECK_METHOD,
                    )
                )
            for piece in (whole.intersection(bad) for bad in merge(damaged)):
                if piece is None or piece.is_instant:
                    continue
                built.append(
                    CoverageInterval(
                        instrument_id,
                        piece.start,
                        piece.end,
                        Quality.DEGRADED,
                        note_over(piece),
                        source_id,
                        CHECK_METHOD,
                    )
                )
        built.sort(key=lambda record: record.start)
        return built


# ----------------------------------------------------------------------------------------
# helpers
# ----------------------------------------------------------------------------------------


def _rows(path: Path):
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            if not any((value or "").strip() for value in row.values()):
                continue
            yield reader.line_num, {(key or "").strip(): value for key, value in row.items()}


def _span(row: dict[str, str], line_number: int) -> Span:
    """The row's span, with UTC attached and ``duration_s`` used as a check on it."""
    start = _moment(row, "start_utc")
    end = _moment(row, "end_utc")
    if end <= start:
        raise ValueError(f"{row.get('folder', '?')} ends at or before it starts")

    stated = (row.get("duration_s") or "").strip()
    if stated:
        measured = (end - start).total_seconds()
        if abs(measured - float(stated)) > DURATION_TOLERANCE_S:
            raise ValueError(
                f"{row.get('folder', '?')} says duration_s={stated} but its timestamps "
                f"span {measured:.1f} s"
            )
    return Span(start, end)


def _moment(row: dict[str, str], column: str) -> datetime:
    text = (row.get(column) or "").strip()
    if not text:
        raise ValueError(f"column {column!r} is required but empty")
    parsed = datetime.fromisoformat(text)
    if parsed.tzinfo is not None:
        return parsed.astimezone(timezone.utc)
    # The column is named for its zone. Attaching UTC here is reading what the header says, not
    # assuming what it left out -- and it is the one place in this codebase allowed to do so.
    return parsed.replace(tzinfo=timezone.utc)


def _summary(
    instrument_id: str, sessions: list[tuple[Span, Quality, str]], built: list[CoverageInterval]
) -> str:
    """One line per instrument, so a run says what it found without anyone opening the JSON."""
    summed = sum(span.duration_s for span, _, _ in sessions) / 3600
    union = sum(span.duration_s for span in merge(span for span, _, _ in sessions)) / 3600
    window = Span(
        min(span.start for span, _, _ in sessions), max(span.end for span, _, _ in sessions)
    )
    span_h = window.duration_s / 3600
    degraded = sum(
        (record.end - record.start).total_seconds()
        for record in built
        if record.quality is Quality.DEGRADED
    ) / 3600
    text = (
        f"{instrument_id}: {len(sessions)} session(s) -> {len(built)} interval(s), "
        f"{union:.2f} h over a {span_h:.2f} h span ({union / span_h * 100:.1f}% duty)"
    )
    if summed - union > 1 / 3600:
        text += f"; {summed - union:.2f} h of session overlap counted once"
    if degraded:
        text += f"; {degraded:.2f} h degraded"
    return text


def _outcome(
    intervals: list[CoverageInterval],
    problems: list[str],
    summaries: list[str],
    skipped: int,
    path: Path,
) -> tuple[SourceStatus, str | None]:
    """Partial success is reported, not hidden: rows that failed are named."""
    notes = list(summaries)
    if skipped:
        notes.append(f"{skipped} row(s) outside the configured season")
    if not problems:
        return SourceStatus.OK, "; ".join(notes) or None

    summary = "; ".join(problems[:5])
    if len(problems) > 5:
        summary += f"; and {len(problems) - 5} more"
    if not intervals:
        return SourceStatus.ERROR, f"no usable rows in {path.name}: {summary}"
    return SourceStatus.STALE, f"{len(problems)} row(s) in {path.name} skipped: {summary}"
