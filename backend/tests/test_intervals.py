from datetime import datetime, timedelta, timezone

import pytest

from availability.core.intervals import (
    build_segments,
    coalesce,
    covers_fully,
    merge,
    sweep,
    total_duration_s,
)
from availability.models import CoverageInterval, Quality, Segment, Span

BASE = datetime(2024, 8, 12, 0, 0, tzinfo=timezone.utc)


def at(hours: float) -> datetime:
    return BASE + timedelta(hours=hours)


def span(start_h: float, end_h: float) -> Span:
    return Span(at(start_h), at(end_h))


def coverage(instrument: str, start_h: float, end_h: float, quality=Quality.GOOD):
    return CoverageInterval(
        instrument_id=instrument, start=at(start_h), end=at(end_h), quality=quality
    )


class TestSpan:
    def test_naive_datetimes_are_rejected(self):
        with pytest.raises(ValueError, match="naive datetime"):
            Span(datetime(2024, 8, 12), datetime(2024, 8, 13))

    def test_backwards_span_is_rejected(self):
        with pytest.raises(ValueError, match="ends before it starts"):
            Span(at(5), at(1))

    def test_half_open_containment(self):
        window = span(1, 2)
        assert window.contains(at(1))
        assert not window.contains(at(2))

    def test_instants_touch_what_holds_them(self):
        instant = Span(at(1.5), at(1.5))
        assert instant.overlaps(span(1, 2))
        assert span(1, 2).overlaps(instant)
        assert not instant.overlaps(span(2, 3))

    def test_abutting_spans_do_not_overlap(self):
        assert not span(1, 2).overlaps(span(2, 3))


class TestMerge:
    def test_overlapping_spans_fuse(self):
        assert merge([span(0, 2), span(1, 3)]) == [span(0, 3)]

    def test_abutting_spans_fuse(self):
        assert merge([span(0, 1), span(1, 2)]) == [span(0, 2)]

    def test_disjoint_spans_survive_separately(self):
        assert merge([span(2, 3), span(0, 1)]) == [span(0, 1), span(2, 3)]

    def test_a_contained_span_does_not_shorten_its_container(self):
        assert merge([span(0, 5), span(1, 2)]) == [span(0, 5)]

    def test_overlap_is_counted_once(self):
        assert total_duration_s([span(0, 2), span(1, 3)]) == 3 * 3600


class TestCoversFully:
    def test_a_gap_defeats_coverage(self):
        assert not covers_fully([span(0, 1), span(2, 3)], span(0, 3))

    def test_abutting_spans_cover_without_a_gap(self):
        assert covers_fully([span(0, 1), span(1, 3)], span(0, 3))

    def test_an_instant_needs_only_to_be_held(self):
        instant = Span(at(1), at(1))
        assert covers_fully([span(0, 2)], instant)
        assert not covers_fully([span(2, 3)], instant)


class TestSweep:
    def test_disjoint_entries_stay_separate(self):
        result = sweep([("a", span(0, 1)), ("b", span(2, 3))])
        assert result == [(span(0, 1), frozenset({"a"})), (span(2, 3), frozenset({"b"}))]

    def test_overlap_is_split_into_three_stretches(self):
        result = sweep([("a", span(0, 2)), ("b", span(1, 3))])
        assert result == [
            (span(0, 1), frozenset({"a"})),
            (span(1, 2), frozenset({"a", "b"})),
            (span(2, 3), frozenset({"b"})),
        ]

    def test_quiet_stretches_are_omitted(self):
        result = sweep([("a", span(0, 1)), ("b", span(5, 6))])
        assert all(keys for _, keys in result)
        assert not any(window.start == at(1) for window, _ in result)

    def test_a_span_inside_another_raises_then_drops_the_degree(self):
        result = sweep([("a", span(0, 4)), ("b", span(1, 2))])
        assert [sorted(keys) for _, keys in result] == [["a"], ["a", "b"], ["a"]]


class TestBuildSegments:
    def test_degree_counts_instruments_not_records(self):
        segments = build_segments(
            [coverage("nimbus", 0, 4), coverage("nimbus", 1, 2), coverage("supersid", 1, 3)]
        )
        overlapping = [s for s in segments if s.degree > 1]
        assert overlapping
        assert all(s.instrument_ids == ("nimbus", "supersid") for s in overlapping)

    def test_worst_quality_wins(self):
        segments = build_segments(
            [coverage("nimbus", 0, 2), coverage("supersid", 0, 2, Quality.DEGRADED)]
        )
        assert len(segments) == 1
        assert segments[0].min_quality is Quality.DEGRADED

    def test_a_quality_change_splits_a_segment(self):
        segments = build_segments(
            [coverage("nimbus", 0, 1), coverage("nimbus", 1, 2, Quality.DEGRADED)]
        )
        assert [s.min_quality for s in segments] == [Quality.GOOD, Quality.DEGRADED]

    def test_touching_identical_segments_fuse(self):
        fused = coalesce(
            [
                Segment(at(0), at(1), ("nimbus",), Quality.GOOD),
                Segment(at(1), at(2), ("nimbus",), Quality.GOOD),
            ]
        )
        assert fused == [Segment(at(0), at(2), ("nimbus",), Quality.GOOD)]

    def test_no_coverage_produces_no_segments(self):
        assert build_segments([]) == []
