"""End to end: configuration through ingest to published snapshot."""

import json

import pytest

from availability.config import Config, ConfigError
from availability.core.snapshot import index_payload, write_snapshot, year_payload
from availability.models import SCHEMA_VERSION, SourceStatus
from availability.store import Store

COVERAGE_CSV = """instrument_id,start,end,quality,note
nimbustrace-seattle,2024-08-11T23:33:00Z,2024-08-12T13:29:00Z,good,
supersid-seattle,2024-08-12T01:12:00Z,2024-08-12T09:34:00Z,good,
nimbustrace-seattle,2024-08-13T20:25:00Z,2024-08-14T01:20:00Z,degraded,last half hour lost
"""

EVENTS_CSV = """kind,time,label,location,magnitude,duration_s,source_ref
fireball,2024-08-12T05:00:00Z,Bright fireball,Washington,-11,7.5,abc123
fireball,2024-08-12T20:00:00Z,Second fireball,Oregon,-9,3.5,def456
fireball,2024-09-30T04:00:00Z,Outside our record,Idaho,-8,2.0,ghi789
"""

CONFIG_TOML = """
[site]
title = "Test record"

[output]
directory = "out"

[[instruments]]
id = "nimbustrace-seattle"
name = "NimbusTrace VLF Receiver"
kind = "vlf"
system = "nimbustrace"
band_hz = {{ low = 0, high = 50000 }}

[[instruments]]
id = "supersid-seattle"
name = "SuperSID VLF Receiver"
kind = "vlf"
system = "supersid"

[[sources]]
id = "manual-coverage"
adapter = "csv_coverage"
kind = "coverage"
name = "Manual coverage record"
path = "{coverage}"

[[sources]]
id = "manual-events"
adapter = "csv_events"
kind = "event"
name = "Manual event record"
path = "{events}"
"""


@pytest.fixture
def config(tmp_path) -> Config:
    (tmp_path / "coverage.csv").write_text(COVERAGE_CSV, encoding="utf-8")
    (tmp_path / "events.csv").write_text(EVENTS_CSV, encoding="utf-8")
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        CONFIG_TOML.format(coverage="coverage.csv", events="events.csv"), encoding="utf-8"
    )
    return Config.load(config_path)


class TestStore:
    def test_sources_report_success(self, config):
        store = Store.build(config)
        assert [source.status for source in store.sources] == [SourceStatus.OK, SourceStatus.OK]
        assert store.warnings == []

    def test_coverage_and_events_are_ingested(self, config):
        store = Store.build(config)
        assert len(store.coverage) == 3
        assert len(store.events) == 3

    def test_overlap_is_derived(self, config):
        store = Store.build(config)
        overlapping = [segment for segment in store.segments if segment.degree == 2]
        assert overlapping
        assert overlapping[0].instrument_ids == ("nimbustrace-seattle", "supersid-seattle")

    def test_verdicts_distinguish_downtime_from_unexamined_time(self, config):
        store = Store.build(config)
        verdicts = {record.event_id: record.verdict.value for record in store.event_coverage}
        assert verdicts["manual-events-abc123"] == "covered"
        assert verdicts["manual-events-def456"] == "not_covered"
        assert verdicts["manual-events-ghi789"] == "unknown"

    def test_known_range_follows_the_data(self, config):
        store = Store.build(config)
        window = store.instrument_map["supersid-seattle"].known_range
        assert window is not None
        assert window.start.hour == 1 and window.end.hour == 9

    def test_coverage_for_an_unknown_instrument_is_dropped_with_a_warning(self, tmp_path, config):
        rogue = tmp_path / "coverage.csv"
        rogue.write_text(
            COVERAGE_CSV + "ghost-instrument,2024-08-12T00:00:00Z,2024-08-12T01:00:00Z,good,\n",
            encoding="utf-8",
        )
        store = Store.build(config)
        assert len(store.coverage) == 3
        assert any("ghost-instrument" in warning for warning in store.warnings)


class TestSnapshot:
    def test_index_lists_instruments_and_years(self, config):
        payload = index_payload(Store.build(config))
        assert payload["schema_version"] == SCHEMA_VERSION
        assert payload["years"] == [2024]
        assert {i["id"] for i in payload["instruments"]} == {
            "nimbustrace-seattle",
            "supersid-seattle",
        }

    def test_year_payload_carries_the_derived_products(self, config):
        payload = year_payload(Store.build(config), 2024)
        assert payload["coverage"] and payload["segments"]
        assert len(payload["events"]) == len(payload["event_coverage"]) == 3

    def test_timestamps_are_utc_with_a_z_suffix(self, config):
        payload = year_payload(Store.build(config), 2024)
        assert all(record["start"].endswith("Z") for record in payload["coverage"])

    def test_snapshot_files_are_written_and_valid_json(self, config, tmp_path):
        store = Store.build(config)
        written = write_snapshot(store, tmp_path / "out")
        assert {path.name for path in written} == {"index.json", "2024.json"}
        for path in written:
            assert json.loads(path.read_text(encoding="utf-8"))["schema_version"] == SCHEMA_VERSION


class TestConfiguration:
    def test_a_broken_source_does_not_sink_the_run(self, tmp_path):
        config_path = tmp_path / "config.toml"
        config_path.write_text(
            CONFIG_TOML.format(coverage="missing.csv", events="missing.csv"), encoding="utf-8"
        )
        store = Store.build(Config.load(config_path))
        assert all(source.status is SourceStatus.ERROR for source in store.sources)
        assert all("no such file" in (source.detail or "") for source in store.sources)

    def test_an_unknown_adapter_is_reported_not_raised(self, tmp_path):
        config_path = tmp_path / "config.toml"
        config_path.write_text(
            """
[[sources]]
id = "nonsense"
adapter = "does_not_exist"
kind = "coverage"
""",
            encoding="utf-8",
        )
        store = Store.build(Config.load(config_path))
        assert store.sources[0].status is SourceStatus.ERROR
        assert "unknown adapter" in store.sources[0].detail

    def test_duplicate_instrument_ids_are_refused(self, tmp_path):
        config_path = tmp_path / "config.toml"
        config_path.write_text(
            """
[[instruments]]
id = "twice"
name = "One"
kind = "vlf"

[[instruments]]
id = "twice"
name = "Two"
kind = "vlf"
""",
            encoding="utf-8",
        )
        with pytest.raises(ConfigError, match="duplicate id"):
            Config.load(config_path)

    def test_an_unknown_instrument_kind_names_the_valid_ones(self, tmp_path):
        config_path = tmp_path / "config.toml"
        config_path.write_text(
            """
[[instruments]]
id = "odd"
name = "Odd"
kind = "telescope"
""",
            encoding="utf-8",
        )
        with pytest.raises(ConfigError, match="unknown kind"):
            Config.load(config_path)
