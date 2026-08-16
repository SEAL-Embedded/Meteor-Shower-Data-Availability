"""Interval algebra over coverage windows.

The one non-obvious routine here is :func:`sweep`, which turns a pile of possibly overlapping
per-instrument intervals into a non-overlapping timeline where each stretch carries the exact set of
instruments recording during it. Everything the front end shows as "overlap" is a filter over that
timeline.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from typing import Hashable, Iterable, Sequence, TypeVar

from ..models import CoverageInterval, Quality, Segment, Span

K = TypeVar("K", bound=Hashable)


def merge(spans: Iterable[Span]) -> list[Span]:
    """Union of *spans*: overlapping and exactly-adjacent spans are fused."""
    ordered = sorted((s for s in spans if not s.is_instant), key=lambda s: (s.start, s.end))
    merged: list[Span] = []
    for span in ordered:
        if merged and span.start <= merged[-1].end:
            if span.end > merged[-1].end:
                merged[-1] = Span(merged[-1].start, span.end)
        else:
            merged.append(span)
    return merged


def total_duration_s(spans: Iterable[Span]) -> float:
    """Total time covered by *spans*, counting overlapping time once."""
    return sum(s.duration_s for s in merge(spans))


def covers_fully(spans: Iterable[Span], target: Span) -> bool:
    """True when the union of *spans* leaves no gap inside *target*."""
    candidates = list(spans)
    if target.is_instant:
        return any(s.overlaps(target) for s in candidates)
    cursor = target.start
    for span in merge(candidates):
        if span.start > cursor:
            return False
        if span.end > cursor:
            cursor = span.end
        if cursor >= target.end:
            return True
    return cursor >= target.end


def sweep(entries: Sequence[tuple[K, Span]]) -> list[tuple[Span, frozenset[K]]]:
    """Flatten keyed, overlapping spans into a non-overlapping timeline.

    Returns ``(span, keys)`` pairs in chronological order, where *keys* is the set of entry keys
    live for the whole of *span*. Stretches with nothing live are omitted.
    """
    deltas: dict = defaultdict(list)
    for key, span in entries:
        if span.is_instant:
            continue
        deltas[span.start].append((key, 1))
        deltas[span.end].append((key, -1))

    result: list[tuple[Span, frozenset[K]]] = []
    live: Counter = Counter()
    previous = None
    for moment in sorted(deltas):
        if previous is not None:
            active = frozenset(k for k, count in live.items() if count > 0)
            if active and moment > previous:
                result.append((Span(previous, moment), active))
        for key, delta in deltas[moment]:
            live[key] += delta
        previous = moment
    return result


def build_segments(coverage: Sequence[CoverageInterval]) -> list[Segment]:
    """Derive the overlap timeline from raw per-instrument coverage.

    Adjacent stretches are fused only when both their instrument set and their worst quality
    match, so a mid-window drop from ``good`` to ``degraded`` stays visible.
    """
    entries = [(index, record.span) for index, record in enumerate(coverage)]
    segments: list[Segment] = []
    for span, keys in sweep(entries):
        records = [coverage[key] for key in keys]
        instrument_ids = tuple(sorted({record.instrument_id for record in records}))
        segments.append(
            Segment(
                start=span.start,
                end=span.end,
                instrument_ids=instrument_ids,
                min_quality=Quality.worst(record.quality for record in records),
            )
        )
    return coalesce(segments)


def coalesce(segments: Sequence[Segment]) -> list[Segment]:
    """Fuse touching segments that are indistinguishable to a reader."""
    fused: list[Segment] = []
    for segment in segments:
        previous = fused[-1] if fused else None
        if (
            previous is not None
            and previous.end == segment.start
            and previous.instrument_ids == segment.instrument_ids
            and previous.min_quality == segment.min_quality
        ):
            fused[-1] = Segment(
                start=previous.start,
                end=segment.end,
                instrument_ids=previous.instrument_ids,
                min_quality=previous.min_quality,
            )
        else:
            fused.append(segment)
    return fused


def clip_segments(segments: Iterable[Segment], window: Span) -> list[Segment]:
    """Restrict *segments* to *window*, dropping those that fall outside it entirely."""
    clipped: list[Segment] = []
    for segment in segments:
        piece = segment.span.intersection(window)
        if piece is None or piece.is_instant:
            continue
        clipped.append(
            Segment(
                start=piece.start,
                end=piece.end,
                instrument_ids=segment.instrument_ids,
                min_quality=segment.min_quality,
            )
        )
    return clipped


def bounding_span(spans: Iterable[Span]) -> Span | None:
    """The smallest span enclosing all of *spans*, or ``None`` if there are none."""
    ordered = list(spans)
    if not ordered:
        return None
    return Span(min(s.start for s in ordered), max(s.end for s in ordered))
