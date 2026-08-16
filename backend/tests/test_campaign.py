"""The dashboard dataset export.

This is a translation between two data models, which is exactly where a value gets quietly
invented. These tests pin the places that matters: what we refuse to claim, and what we must not
lose.
"""

import json
from datetime import datetime, timezone

import pytest

from availability.config import Config
from availability.core.campaign import campaign_payload
from availability.core.snapshot import write_campaign
from availability.store import Store

COVERAGE_CSV = """instrument_id,start,end,quality,note
sphere-vlf-seattle,2024-07-29T00:00:00Z,2024-07-29T01:00:00Z,degraded,Minor Data Lost Can be Recovered
sphere-vlf-seattle,2024-07-29T01:00:00Z,2024-07-29T10:00:00Z,good,
sphere-vlf-seattle,2024-07-29T10:00:00Z,2024-07-29T11:00:00Z,lost,Major Data Lost
sky-camera-seattle,2024-07-29T03:00:00Z,2024-07-29T06:00:00Z,good,
"""

EVENTS_CSV = """kind,time,label,location,magnitude,duration_s,time_uncertainty_s,url,source_ref
fireball,2024-07-29T05:00:00Z,Fireball over Washington,Washington (US),-11.5,7.5,60,https://example.org/e/1,3646-2024
rocket_launch,2024-12-01T05:00:00Z,A launch well outside the season,Florida,,,,,f9-1
"""

CONFIG_TOML = """
[output]
directory = "out"

[[instruments]]
id = "sphere-vlf-seattle"
name = "Sphere VLF Antenna System — Seattle"
kind = "vlf"
system = "sphere"
site = {{ id = "seattle", name = "Seattle, Washington", latitude = 47.6553, longitude = -122.3035 }}

[[instruments]]
id = "sky-camera-seattle"
name = "Sky Camera — Seattle"
kind = "sky_camera"
system = "skycam"

[[sources]]
id = "record-2024"
adapter = "csv_coverage"
kind = "coverage"
name = "2024 season"
path = "coverage.csv"

[[sources]]
id = "ams"
adapter = "csv_events"
kind = "event"
name = "American Meteor Society"
path = "events.csv"

[campaign]
enabled = true
path = "{path}"
site_altitude_m = 45

[campaign.instrument_ids]
"sphere-vlf-seattle" = "sphere_antenna"
"sky-camera-seattle" = "skycam"

[campaign.provenance]
"record-2024" = "sheet_2024"
"ams" = "ams_scrape"
"""


@pytest.fixture
def config(tmp_path) -> Config:
    (tmp_path / "coverage.csv").write_text(COVERAGE_CSV, encoding="utf-8")
    (tmp_path / "events.csv").write_text(EVENTS_CSV, encoding="utf-8")
    (tmp_path / "config.toml").write_text(
        CONFIG_TOML.format(path="data/2024-campaign.json"), encoding="utf-8"
    )
    return Config.load(tmp_path / "config.toml")


@pytest.fixture
def payload(config) -> dict:
    return campaign_payload(Store.build(config), config.campaign)


def ms(text: str) -> int:
    return int(datetime.fromisoformat(text).replace(tzinfo=timezone.utc).timestamp() * 1000)


class TestShape:
    def test_it_carries_the_sections_the_dashboard_reads(self, payload):
        assert set(payload) >= {"meta", "site", "campaign", "instruments", "coverage", "events"}

    def test_timestamps_are_epoch_milliseconds(self, payload):
        record = payload["coverage"][0]
        assert record["start"] == ms("2024-07-29T00:00:00")
        assert isinstance(record["start"], int)

    def test_instrument_ids_are_translated(self, payload):
        assert {i["id"] for i in payload["instruments"]} == {"sphere_antenna", "skycam"}
        assert {r["instrumentId"] for r in payload["coverage"]} == {"sphere_antenna", "skycam"}

    def test_the_site_is_declared_not_assumed(self, payload):
        assert payload["site"]["assumed"] is False
        assert payload["site"]["lat"] == 47.6553


class TestCampaignWindow:
    def test_it_spans_the_observing_period_not_the_event_period(self, payload):
        """An out-of-season catalogue event must not stretch the campaign across the year."""
        assert payload["campaign"]["start"] == ms("2024-07-29T00:00:00")
        assert payload["campaign"]["end"] == ms("2024-07-29T11:00:00")

    def test_the_out_of_season_event_is_still_present(self, payload):
        """Narrowing the window must not drop records."""
        assert any(e["eventClass"] == "launch" for e in payload["events"])


class TestQuality:
    def test_quality_maps_to_status_and_loss(self, payload):
        by_status = {r["status"]: r for r in payload["coverage"]}
        assert by_status["ok"]["lossSeverity"] == "none"
        assert by_status["partial"]["lossSeverity"] == "minor"
        assert by_status["corrupt"]["lossSeverity"] == "major"

    def test_the_operators_check_is_carried_not_discarded(self, payload):
        """The season sheet is a check -- a human one -- and must not report as unchecked.

        The dashboard draws unchecked coverage as a hollow outline, so reporting it that way made
        a fully characterised season render as if nobody had ever looked at it.
        """
        by_status = {r["status"]: r for r in payload["coverage"]}
        assert by_status["ok"]["validation"] == "valid"
        assert by_status["partial"]["validation"] == "valid"
        assert by_status["corrupt"]["validation"] == "invalid"

    def test_the_check_method_says_how_it_was_checked(self, payload):
        """Not one of the scanner's methods, because no scanner ran."""
        assert {r["checkMethod"] for r in payload["coverage"]} == {"operator_log"}

    def test_recoverability_is_read_from_the_note(self, payload):
        recoverable = [r for r in payload["coverage"] if r.get("lossRecoverable")]
        assert len(recoverable) == 1
        assert "Recovered" in recoverable[0]["label"]

    def test_fields_we_do_not_hold_are_omitted_not_defaulted(self, payload):
        """Absent means "not determined"; the dashboard supplies its own defaults."""
        clean = next(r for r in payload["coverage"] if r["status"] == "ok")
        assert "publishState" not in clean
        assert "clockQuality" not in clean
        assert "label" not in clean


class TestEvents:
    @pytest.fixture
    def fireball(self, payload):
        return next(e for e in payload["events"] if e["eventClass"] == "fireball")

    def test_magnitude_is_labelled_as_a_witness_estimate(self, fireball):
        assert fireball["magnitudeValue"] == -11.5
        assert fireball["magnitudeBasis"] == "witness_estimate"

    def test_timing_uncertainty_is_reported_precision(self, fireball):
        assert fireball["uncertaintyBasis"] == "reported_precision"
        assert fireball["uncertaintySec"] == 60

    def test_identity_and_link_survive(self, fireball):
        assert fireball["eventRefId"] == "3646-2024"
        assert fireball["referenceUrl"] == "https://example.org/e/1"

    def test_provenance_distinguishes_the_sources(self, payload):
        assert {e["provenance"] for e in payload["events"]} == {"ams_scrape"}
        assert {r["provenance"] for r in payload["coverage"]} == {"sheet_2024"}

    def test_an_event_without_a_magnitude_omits_the_field(self, payload):
        launch = next(e for e in payload["events"] if e["eventClass"] == "launch")
        assert "magnitudeValue" not in launch
        assert "magnitudeBasis" not in launch


class TestWriting:
    def test_it_writes_where_the_dashboard_looks(self, config, tmp_path):
        target = write_campaign(Store.build(config), config)
        assert target == tmp_path / "data" / "2024-campaign.json"
        assert json.loads(target.read_text(encoding="utf-8"))["coverage"]
