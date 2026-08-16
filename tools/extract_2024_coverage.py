"""Derive the 2024 coverage record from the hand-kept season spreadsheet.

The spreadsheet is the record. This script transcribes it; it does not interpret it. Three rules,
and every one of them is reported rather than applied quietly:

1. **The column is the clock.** Each operating-hours grid has a column per hour, so a cell in the
   22:00 column reading ``E: 10:53`` has a mistyped hour, not a time. The column wins and the
   correction is printed.
2. **Unpaired marks are dropped, never completed.** A start with no end is a period the sheet does
   not describe. It is left out, so the site reports it as uncharacterised rather than inventing
   uptime that was never recorded.
3. **Data-loss notes downgrade quality.** The daily log records where data was impaired or lost.
   Those hours are split out of their interval and marked, carrying the sheet's exact wording.

Usage::

    python tools/extract_2024_coverage.py <sheet-dump.json> [--out records/coverage-2024.csv]

where the dump is ``{"fileContent": "<the sheet as markdown tables>"}``.
"""

from __future__ import annotations

import argparse
import json
import re
from datetime import datetime, timedelta
from pathlib import Path

YEAR = 2024

MONTHS = {
    "january": 1, "february": 2, "march": 3, "april": 4, "may": 5, "june": 6,
    "july": 7, "august": 8, "september": 9, "october": 10, "november": 11, "december": 12,
}

MARK = re.compile(r"(?P<kind>[SE])\s*:?\s*(?P<hour>\d{1,2}):(?P<minute>\d{2})", re.IGNORECASE)
DATE_CELL = re.compile(r"^(?P<month>[A-Za-z]+)\s+(?P<day>\d{1,2})$")
HOUR_CELL = re.compile(r"^(?P<hour>\d{1,2}):00$")

#: Grid headings and daily-log column headings both name the instrument; this maps either to an id.
INSTRUMENTS = (
    ("sphere", "sphere-vlf-seattle"),
    ("magnetometer", "vectaire-magnetometer-seattle"),
    ("sky cam", "sky-camera-seattle"),
    ("sky camera", "sky-camera-seattle"),
)


def instrument_for(text: str) -> str | None:
    lowered = text.lower()
    for needle, identifier in INSTRUMENTS:
        if needle in lowered:
            return identifier
    return None


def cells(line: str) -> list[str]:
    parts = [c.strip() for c in line.split("|")]
    return parts[1:-1] if len(parts) > 2 else []


# ------------------------------------------------------------------------------------------
# operating-hours grids
# ------------------------------------------------------------------------------------------


SEPARATOR = ":-:"
HEADING_LOOKBACK = 12


def read_grids(lines: list[str]) -> list[dict]:
    """Each 24-hour operating grid, with the instrument heading that introduces it.

    The heading is found by walking back from the grid's header row to the nearest line naming an
    instrument. An earlier version carried a rolling buffer instead, and when one grid's heading
    was missed it silently inherited the previous grid's -- which attributed a whole instrument's
    recordings to a different instrument. Deterministic lookup, and a grid with no heading of its
    own is refused rather than guessed at.
    """
    grids: list[dict] = []

    for index, line in enumerate(lines):
        row = cells(line)
        if not (row and row[0].lower().startswith("time") and any(c == "0:00" for c in row[1:3])):
            continue

        rows: list[list[str]] = []
        for follow in lines[index + 1 :]:
            following = cells(follow)
            if not following or not following[0] or following[0].startswith(SEPARATOR):
                continue
            if DATE_CELL.match(following[0]):
                rows.append(following)
            else:
                break  # a new section has started

        grids.append({"header": row, "rows": rows, "heading": heading_before(lines, index)})
    return grids


def heading_before(lines: list[str], index: int) -> str:
    """The nearest preceding line that names an instrument."""
    for line in reversed(lines[max(0, index - HEADING_LOOKBACK) : index]):
        text = " ".join(c for c in cells(line) if c and not c.startswith(SEPARATOR))
        if instrument_for(text):
            return text
    return ""


class Mark:
    """One start or end, with the hour its column implies kept alongside the hour it states."""

    __slots__ = ("moment", "kind", "column_moment", "where")

    def __init__(self, moment: datetime, kind: str, column_moment: datetime | None, where: str):
        self.moment = moment
        self.kind = kind
        self.column_moment = column_moment
        self.where = where


def grid_marks(grid: dict) -> list[Mark]:
    header = grid["header"]
    found: list[Mark] = []

    for row in grid["rows"]:
        date_match = DATE_CELL.match(row[0])
        month = MONTHS[date_match.group("month").lower()]
        day = int(date_match.group("day"))

        for index in range(1, min(len(row), len(header))):
            cell = row[index]
            if not cell:
                continue
            column = HOUR_CELL.match(header[index])
            column_hour = int(column.group("hour")) if column else None

            for mark in MARK.finditer(cell):
                hour, minute = int(mark.group("hour")), int(mark.group("minute"))
                if hour > 23 or minute > 59:
                    continue
                stated = datetime(YEAR, month, day, hour, minute)
                implied = (
                    datetime(YEAR, month, day, column_hour, minute)
                    if column_hour is not None and column_hour != hour
                    else None
                )
                found.append(
                    Mark(
                        stated,
                        mark.group("kind").upper(),
                        implied,
                        f"{date_match.group(0)} column {header[index]}, cell {mark.group(0)!r}",
                    )
                )
    return found


def pair(marks: list[Mark]) -> tuple[list[tuple[datetime, datetime]], list[str], list[str]]:
    """A start opens an interval, the next end closes it.

    The stated time is believed. The column is consulted only when a stated time breaks the
    sequence -- a cell reading ``E: 10:53`` sitting in the 22:00 column, after a start at 20:40,
    is a mistyped hour. Where the column would merely disagree without the sequence being broken,
    the sheet is left alone: an overnight run genuinely ending at 09:34 is not wrong just because
    its cell drifted a couple of columns.
    """
    intervals: list[tuple[datetime, datetime]] = []
    repairs: list[str] = []
    dropped: list[str] = []
    open_at: datetime | None = None

    for mark in marks:
        if mark.kind == "S":
            if open_at is not None:
                dropped.append(
                    f"start {open_at:%b %d %H:%M} never closed (a later start at "
                    f"{mark.moment:%b %d %H:%M} replaced it)"
                )
            open_at = mark.moment
            continue

        moment = mark.moment
        if open_at is not None and moment <= open_at and mark.column_moment is not None:
            if mark.column_moment > open_at:
                repairs.append(
                    f"{mark.where}: read as {mark.column_moment:%b %d %H:%M}; the stated hour "
                    f"precedes the start at {open_at:%b %d %H:%M}, the column does not"
                )
                moment = mark.column_moment

        if open_at is None:
            dropped.append(f"end {moment:%b %d %H:%M} with nothing open")
        elif moment <= open_at:
            dropped.append(f"end {moment:%b %d %H:%M} is not after start {open_at:%b %d %H:%M}")
            open_at = None
        else:
            intervals.append((open_at, moment))
            open_at = None

    if open_at is not None:
        dropped.append(f"start {open_at:%b %d %H:%M} never closed")
    return intervals, repairs, dropped


# ------------------------------------------------------------------------------------------
# data-loss notes from the daily log
# ------------------------------------------------------------------------------------------


def read_notes(lines: list[str]) -> list[tuple[str, datetime, datetime, str, str]]:
    """(instrument_id, start, end, quality, wording) for each recorded data-loss note."""
    notes: list[tuple[str, datetime, datetime, str, str]] = []
    columns: dict[int, str] = {}
    seen: set[tuple] = set()
    current_date: tuple[int, int] | None = None

    for line in lines:
        row = cells(line)
        if len(row) < 3:
            continue

        if row[0].lower().startswith("date") and "hour" in row[1].lower():
            columns = {}
            for index, header in enumerate(row):
                identifier = instrument_for(header)
                if identifier and index >= 2:
                    columns.setdefault(index, identifier)
            current_date = None
            continue

        date_match = DATE_CELL.match(row[0])
        if date_match:
            current_date = (MONTHS[date_match.group("month").lower()], int(date_match.group("day")))

        hour_match = HOUR_CELL.match(row[1]) if len(row) > 1 else None
        if not (columns and current_date and hour_match):
            continue

        for index, identifier in columns.items():
            if index >= len(row):
                continue
            cell = row[index]
            if "lost" not in cell.lower():
                continue
            start = datetime(YEAR, current_date[0], current_date[1], int(hour_match.group("hour")))
            quality = "lost" if "major" in cell.lower() else "degraded"
            wording = " ".join(cell.split())
            key = (identifier, start, wording)
            if key in seen:
                continue  # the same note appears on more than one tab
            seen.add(key)
            notes.append((identifier, start, start + timedelta(hours=1), quality, wording))
    return notes


def apply_notes(rows: list[dict], notes: list) -> tuple[list[dict], list[str]]:
    """Split intervals so annotated hours carry their own quality."""
    applied: list[str] = []
    for identifier, start, end, quality, wording in notes:
        result: list[dict] = []
        hit = False
        for row in rows:
            if row["instrument_id"] != identifier or row["end"] <= start or row["start"] >= end:
                result.append(row)
                continue
            hit = True
            overlap_start = max(row["start"], start)
            overlap_end = min(row["end"], end)
            if row["start"] < overlap_start:
                result.append({**row, "end": overlap_start})
            result.append(
                {
                    "instrument_id": identifier,
                    "start": overlap_start,
                    "end": overlap_end,
                    "quality": quality,
                    "note": wording,
                }
            )
            if overlap_end < row["end"]:
                result.append({**row, "start": overlap_end})
        rows = result
        applied.append(
            f"{start:%b %d %H:%M} {identifier}: {quality} -- {wording}"
            + ("" if hit else "   [NOT APPLIED: no recording interval covers that hour]")
        )
    return rows, applied


# ------------------------------------------------------------------------------------------


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("dump", help="JSON dump of the sheet")
    parser.add_argument("--out", default="records/coverage-2024.csv")
    args = parser.parse_args()

    raw = json.loads(Path(args.dump).read_text(encoding="utf-8"))["fileContent"]
    lines = raw.splitlines()

    repairs: list[str] = []
    dropped: list[str] = []
    rows: list[dict] = []
    seen_instruments: list[str] = []

    for grid in read_grids(lines):
        identifier = instrument_for(grid["heading"])
        if identifier is None:
            print(
                f"! REFUSED: a grid of {len(grid['rows'])} rows has no instrument heading of its "
                f"own and was not imported. Guessing which instrument it belongs to would "
                f"attribute one instrument's recordings to another."
            )
            continue
        if identifier in seen_instruments:
            print(
                f"! WARNING: {identifier} claimed by more than one grid -- check the headings, "
                f"this is how a mislabelled grid looks."
            )
        seen_instruments.append(identifier)

        intervals, grid_repairs, grid_dropped = pair(grid_marks(grid))
        repairs.extend(f"{identifier}: {item}" for item in grid_repairs)
        dropped.extend(f"{identifier}: {item}" for item in grid_dropped)
        rows.extend(
            {"instrument_id": identifier, "start": s, "end": e, "quality": "good", "note": ""}
            for s, e in intervals
        )

    notes = read_notes(lines)
    rows, applied = apply_notes(rows, notes)
    rows.sort(key=lambda row: (row["start"], row["instrument_id"]))

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", encoding="utf-8", newline="") as handle:
        handle.write("instrument_id,start,end,quality,note\n")
        for row in rows:
            note = row["note"].replace('"', "'")
            quoted = f'"{note}"' if "," in note else note
            handle.write(
                f"{row['instrument_id']},{row['start']:%Y-%m-%dT%H:%M:%S}Z,"
                f"{row['end']:%Y-%m-%dT%H:%M:%S}Z,{row['quality']},{quoted}\n"
            )

    print(f"REPAIRED ({len(repairs)}) -- a stated hour broke the sequence, the column resolved it:")
    for item in repairs:
        print(f"  {item}")

    print(f"\nQUALITY NOTES APPLIED ({len(applied)}):")
    for item in applied:
        print(f"  {item}")

    print(f"\nLEFT OUT ({len(dropped)}) -- the sheet does not say, so neither does the record:")
    for item in dropped:
        print(f"  {item}")

    by_quality: dict[str, int] = {}
    for row in rows:
        by_quality[row["quality"]] = by_quality.get(row["quality"], 0) + 1
    print(f"\nwrote {len(rows)} interval(s) to {out}")
    print("  " + ", ".join(f"{count} {quality}" for quality, count in sorted(by_quality.items())))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
