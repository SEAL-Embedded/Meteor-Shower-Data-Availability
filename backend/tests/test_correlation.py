from datetime import datetime, timedelta, timezone

from availability.core.correlation import classify_events, event_window
from availability.models import (
    CoverageInterval,
    Event,
    EventKind,
    Instrument,
    InstrumentKind,
    Quality,
    Span,
    Verdict,
)

BASE = datetime(2024, 8, 12, 0, 0, tzinfo=timezone.utc)


def at(hours: float) -> datetime:
    return BASE + timedelta(hours=hours)


def instrument(identifier: str, known_from: float = 0, known_to: float = 24) -> Instrument:
    return Instrument(
        id=identifier,
        name=identifier,
        kind=InstrumentKind.VLF,
        system=identifier,
        known_range=Span(at(known_from), at(known_to)),
    )


def coverage(identifier: str, start_h: float, end_h: float, quality=Quality.GOOD):
    return CoverageInterval(
        instrument_id=identifier, start=at(start_h), end=at(end_h), quality=quality
    )


def fireball(hours: float, uncertainty_s: float | None = None, identifier: str = "e1") -> Event:
    return Event(
        id=identifier,
        source_id="test",
        kind=EventKind.FIREBALL,
        time=at(hours),
        time_uncertainty_s=uncertainty_s,
    )


def verdict_for(events, coverage_records, instruments) -> Verdict:
    return classify_events(events, coverage_records, instruments)[0].verdict


class TestEventWindow:
    def test_a_point_event_is_an_instant(self):
        assert event_window(fireball(3)).is_instant

    def test_uncertainty_widens_both_ways(self):
        window = event_window(fireball(3, uncertainty_s=60))
        assert window.start == at(3) - timedelta(seconds=60)
        assert window.end == at(3) + timedelta(seconds=60)

    def test_an_event_with_an_end_keeps_its_duration(self):
        launch = Event(
            id="launch",
            source_id="test",
            kind=EventKind.ROCKET_LAUNCH,
            time=at(5),
            end_time=at(8),
        )
        assert event_window(launch) == Span(at(5), at(8))


class TestVerdicts:
    def test_recording_cleanly_is_covered(self):
        assert (
            verdict_for([fireball(3)], [coverage("nimbus", 1, 5)], [instrument("nimbus")])
            is Verdict.COVERED
        )

    def test_degraded_data_is_only_partial(self):
        assert (
            verdict_for(
                [fireball(3)],
                [coverage("nimbus", 1, 5, Quality.DEGRADED)],
                [instrument("nimbus")],
            )
            is Verdict.PARTIAL
        )

    def test_lost_data_is_partial_not_covered(self):
        assert (
            verdict_for(
                [fireball(3)], [coverage("nimbus", 1, 5, Quality.LOST)], [instrument("nimbus")]
            )
            is Verdict.PARTIAL
        )

    def test_a_gap_inside_a_characterised_day_is_not_covered(self):
        assert (
            verdict_for([fireball(3)], [coverage("nimbus", 5, 9)], [instrument("nimbus")])
            is Verdict.NOT_COVERED
        )

    def test_outside_every_characterised_period_is_unknown(self):
        assert (
            verdict_for(
                [fireball(30)], [coverage("nimbus", 5, 9)], [instrument("nimbus", 0, 24)]
            )
            is Verdict.UNKNOWN
        )

    def test_unknown_is_never_reported_as_downtime(self):
        """The distinction the whole record depends on."""
        never_characterised = Instrument(
            id="nimbus", name="nimbus", kind=InstrumentKind.VLF, system="nimbus", known_range=None
        )
        assert verdict_for([fireball(3)], [], [never_characterised]) is Verdict.UNKNOWN

    def test_a_duration_event_needs_its_whole_window_covered(self):
        launch = Event(
            id="launch",
            source_id="test",
            kind=EventKind.ROCKET_LAUNCH,
            time=at(2),
            end_time=at(6),
        )
        assert (
            verdict_for([launch], [coverage("nimbus", 1, 4)], [instrument("nimbus")])
            is Verdict.PARTIAL
        )
        assert (
            verdict_for([launch], [coverage("nimbus", 1, 7)], [instrument("nimbus")])
            is Verdict.COVERED
        )

    def test_two_abutting_clean_intervals_cover_a_duration_event(self):
        launch = Event(
            id="launch",
            source_id="test",
            kind=EventKind.ROCKET_LAUNCH,
            time=at(2),
            end_time=at(6),
        )
        records = [coverage("nimbus", 1, 4), coverage("nimbus", 4, 7)]
        assert verdict_for([launch], records, [instrument("nimbus")]) is Verdict.COVERED

    def test_uncertainty_can_pull_an_event_into_coverage(self):
        just_after = fireball(5, uncertainty_s=600)
        assert (
            verdict_for([just_after], [coverage("nimbus", 1, 5)], [instrument("nimbus")])
            is Verdict.PARTIAL
        )


class TestCovering:
    def test_every_recording_instrument_is_listed(self):
        result = classify_events(
            [fireball(3)],
            [coverage("nimbus", 1, 5), coverage("supersid", 2, 4, Quality.DEGRADED)],
            [instrument("nimbus"), instrument("supersid")],
        )[0]
        assert result.degree == 2
        assert result.verdict is Verdict.COVERED
        assert {c.instrument_id: c.quality for c in result.covering} == {
            "nimbus": Quality.GOOD,
            "supersid": Quality.DEGRADED,
        }

    def test_nothing_recording_means_nothing_listed(self):
        result = classify_events([fireball(3)], [], [instrument("nimbus")])[0]
        assert result.degree == 0
        assert result.covering == ()
