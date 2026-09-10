"""The archive-index adapter.

The two properties worth pinning here are the two that were got wrong in the published record
before this adapter existed: overlapping sessions must be counted once, and a session that did not
save cleanly must mark the minutes it covers rather than the run it sits in.
"""

from datetime import datetime, timezone

import pytest

from availability.config import Config
from availability.core.campaign import campaign_payload
from availability.models import Quality, SourceStatus
from availability.store import Store

# Two clean NimbusTrace blocks, then an interrupted one that starts ten seconds before the second
# ends -- the overlap-plus-degraded case, which is where a naive merge either double-counts or
# swallows the whole run.
INDEX = """instrument,folder,start_utc,end_utc,duration_s,note
NimbusTrace,Data-2026-08-12T00-00-00_completely_saved,2026-08-12 00:00:00,2026-08-12 02:00:00,7200.0,completely
NimbusTrace,Data-2026-08-12T02-00-00_completely_saved,2026-08-12 02:00:00,2026-08-12 04:00:00,7200.0,completely
NimbusTrace,Data-2026-08-12T03-59-50_break_saved,2026-08-12 03:59:50,2026-08-12 04:15:00,910.0,break
SuperSID,SuperSID-0812T00-00-00,2026-08-12 00:00:00,2026-08-12 10:00:00,36000.0,96000Hz/1ch
SuperSID,SuperSID-0812T05-00-00,2026-08-12 05:00:00,2026-08-12 12:00:00,25200.0,96000Hz/1ch
"""

CONFIG_TOML = """
[output]
directory = "out"

[[instruments]]
id = "nimbustrace-seattle"
name = "NimbusTrace VLF Receiver — Seattle"
kind = "vlf"
system = "nimbustrace"
clock = {{ quality = "free_running", note = "+73.3 ppm fast" }}

[[instruments]]
id = "supersid-seattle"
name = "SuperSID VLF Receiver — Seattle"
kind = "vlf"
system = "supersid"

[[sources]]
id = "archive-2026"
adapter = "archive_sessions"
kind = "coverage"
name = "2026 archive scan"
path = "index.csv"
instruments = {{ NimbusTrace = "nimbustrace-seattle", SuperSID = "supersid-seattle" }}
clean_note = {{ NimbusTrace = "completely", SuperSID = "96000Hz/1ch" }}
{extra}

[campaign]
enabled = true
path = "campaign.json"

[campaign.instrument_ids]
"nimbustrace-seattle" = "nimbustrace"
"supersid-seattle" = "supersid"

[campaign.publish_state]
"archive-2026" = "{publish_state}"
"""


def build(tmp_path, index: str = INDEX, extra: str = "", publish_state: str = "publishable"):
    (tmp_path / "index.csv").write_text(index, encoding="utf-8")
    (tmp_path / "config.toml").write_text(
        CONFIG_TOML.format(extra=extra, publish_state=publish_state), encoding="utf-8"
    )
    return Config.load(tmp_path / "config.toml")


def at(text: str) -> datetime:
    return datetime.fromisoformat(text).replace(tzinfo=timezone.utc)


def coverage_for(store: Store, instrument_id: str):
    return [r for r in store.coverage if r.instrument_id == instrument_id]


def hours(records) -> float:
    return sum((r.end - r.start).total_seconds() for r in records) / 3600


class TestUnion:
    def test_overlapping_sessions_are_counted_once(self, tmp_path):
        """Ten hours and seven hours that overlap by five are twelve hours, not seventeen."""
        store = Store.build(build(tmp_path))
        records = coverage_for(store, "supersid-seattle")
        assert hours(records) == pytest.approx(12.0)

    def test_a_continuous_run_becomes_one_interval(self, tmp_path):
        store = Store.build(build(tmp_path))
        records = coverage_for(store, "supersid-seattle")
        assert len(records) == 1
        assert records[0].start == at("2026-08-12 00:00:00")
        assert records[0].end == at("2026-08-12 12:00:00")

    def test_the_pieces_tile_the_run_exactly(self, tmp_path):
        """No gap and no overlap between emitted intervals, so summing them is safe."""
        store = Store.build(build(tmp_path))
        records = sorted(coverage_for(store, "nimbustrace-seattle"), key=lambda r: r.start)
        for earlier, later in zip(records, records[1:]):
            assert earlier.end == later.start
        assert hours(records) == pytest.approx(4.25)


class TestQuality:
    def test_an_interrupted_session_does_not_grade_the_whole_run(self, tmp_path):
        """Fifteen bad minutes at the end of four hours mark fifteen minutes, not four hours."""
        store = Store.build(build(tmp_path))
        records = sorted(coverage_for(store, "nimbustrace-seattle"), key=lambda r: r.start)
        assert [r.quality for r in records] == [Quality.GOOD, Quality.DEGRADED]
        assert hours([r for r in records if r.quality is Quality.GOOD]) == pytest.approx(
            3 + 59 / 60 + 50 / 3600
        )

    def test_overlap_between_whole_and_interrupted_resolves_to_degraded(self, tmp_path):
        """The ten seconds both sessions cover are not claimed as whole data."""
        store = Store.build(build(tmp_path))
        degraded = [
            r for r in coverage_for(store, "nimbustrace-seattle") if r.quality is Quality.DEGRADED
        ]
        assert len(degraded) == 1
        assert degraded[0].start == at("2026-08-12 03:59:50")
        assert degraded[0].end == at("2026-08-12 04:15:00")

    def test_the_degraded_interval_says_what_marked_it(self, tmp_path):
        store = Store.build(build(tmp_path))
        degraded = next(
            r for r in coverage_for(store, "nimbustrace-seattle") if r.quality is Quality.DEGRADED
        )
        assert degraded.note == "save marker: break"

    def test_an_unanticipated_marker_reads_as_suspect(self, tmp_path):
        """Config states the good value, so a marker nobody expected cannot pass as clean."""
        index = INDEX.replace(",completely\nSuperSID", ",NO_SAVE_MARKER\nSuperSID")
        store = Store.build(build(tmp_path, index=index))
        records = coverage_for(store, "nimbustrace-seattle")
        assert any(r.quality is Quality.DEGRADED for r in records)
        assert hours(records) == pytest.approx(4.25)


class TestCharacterisedPeriod:
    def test_the_scanned_span_is_wider_than_the_recordings(self, tmp_path):
        """A gap between sessions is downtime, which needs the whole span to have been examined."""
        index = INDEX + (
            "SuperSID,SuperSID-0813T00-00-00,2026-08-13 00:00:00,"
            "2026-08-13 01:00:00,3600.0,96000Hz/1ch\n"
        )
        store = Store.build(build(tmp_path, index=index))
        known = store.instrument_map["supersid-seattle"].known_range
        assert known.start == at("2026-08-12 00:00:00")
        assert known.end == at("2026-08-13 01:00:00")


class TestDamagedRows:
    def test_a_row_whose_duration_contradicts_its_timestamps_is_refused(self, tmp_path):
        index = INDEX.replace("2026-08-12 02:00:00,7200.0", "2026-08-12 02:00:00,60.0")
        store = Store.build(build(tmp_path, index=index))
        source = next(s for s in store.sources if s.id == "archive-2026")
        assert source.status is SourceStatus.STALE
        assert "duration_s" in source.detail

    def test_a_row_for_an_unmapped_instrument_is_reported_not_guessed(self, tmp_path):
        index = INDEX + "Magnetometer,M-01,2026-08-12 00:00:00,2026-08-12 01:00:00,3600.0,ok\n"
        store = Store.build(build(tmp_path, index=index))
        source = next(s for s in store.sources if s.id == "archive-2026")
        assert source.status is SourceStatus.STALE
        assert "Magnetometer" in source.detail

    def test_a_missing_index_names_every_path_it_tried(self, tmp_path):
        config = build(tmp_path)
        (tmp_path / "index.csv").unlink()
        store = Store.build(config)
        source = next(s for s in store.sources if s.id == "archive-2026")
        assert source.status is SourceStatus.ERROR
        assert "index.csv" in source.detail
        assert not store.coverage


class TestSeason:
    def test_rows_outside_the_configured_season_are_left_alone(self, tmp_path):
        index = INDEX + (
            "SuperSID,SuperSID-2025,2025-08-12 00:00:00,2025-08-12 01:00:00,3600.0,96000Hz/1ch\n"
        )
        store = Store.build(build(tmp_path, index=index, extra="season = 2026"))
        assert all(r.start.year == 2026 for r in store.coverage)
        source = next(s for s in store.sources if s.id == "archive-2026")
        assert "outside the configured season" in source.detail


class TestReporting:
    def test_the_run_says_what_it_found_without_opening_the_json(self, tmp_path):
        store = Store.build(build(tmp_path))
        detail = next(s for s in store.sources if s.id == "archive-2026").detail
        assert "supersid-seattle" in detail
        assert "5.00 h of session overlap counted once" in detail
        assert "% duty" in detail


class TestPublishedShape:
    def test_the_dashboard_is_told_these_were_scanned_not_transcribed(self, tmp_path):
        config = build(tmp_path)
        payload = campaign_payload(Store.build(config), config.campaign)
        assert {r["checkMethod"] for r in payload["coverage"]} == {"archive_scan"}

    def test_a_characterised_clock_travels_with_every_interval(self, tmp_path):
        config = build(tmp_path)
        payload = campaign_payload(Store.build(config), config.campaign)
        nimbus = [r for r in payload["coverage"] if r["instrumentId"] == "nimbustrace"]
        assert nimbus
        assert all(r["clockQuality"] == "free_running" for r in nimbus)
        assert all("+73.3 ppm fast" in r["clockNote"] for r in nimbus)

    def test_an_unmeasured_clock_is_left_absent_rather_than_asserted(self, tmp_path):
        """The dashboard's own default is "unknown"; inventing anything else moves a headline."""
        config = build(tmp_path)
        payload = campaign_payload(Store.build(config), config.campaign)
        supersid = [r for r in payload["coverage"] if r["instrumentId"] == "supersid"]
        assert supersid
        assert all("clockQuality" not in r for r in supersid)

    def test_publish_state_is_said_only_where_the_config_states_it(self, tmp_path):
        config = build(tmp_path)
        payload = campaign_payload(Store.build(config), config.campaign)
        assert {r["publishState"] for r in payload["coverage"]} == {"publishable"}

    def test_a_source_the_config_does_not_name_keeps_the_dashboard_default(self, tmp_path):
        """Absent, not "draft": how far a record has been reviewed is not the scan's to assert."""
        config = build(tmp_path)
        config.campaign.publish_state = {}
        payload = campaign_payload(Store.build(config), config.campaign)
        assert all("publishState" not in r for r in payload["coverage"])

    def test_a_state_the_dashboard_cannot_count_is_refused_at_load(self, tmp_path):
        """A typo would otherwise publish records into a state nothing counts."""
        from availability.config import ConfigError

        with pytest.raises(ConfigError, match="publish_state"):
            build(tmp_path, publish_state="publishible")
